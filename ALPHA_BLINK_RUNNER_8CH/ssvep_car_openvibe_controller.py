"""OpenViBE Python Box for the EEG Horizon Run car game.

OpenViBE supplies already acquired, filtered and epoched EEG. This box uses
the same regularized CCA-style reference scoring as the alpha-detector demo,
then sends confirmed left/right commands to brain_bridge.py over localhost.
"""

import json
import time
import urllib.parse
import urllib.request

import numpy


class MyOVBox(OVBox):
    def __init__(self):
        OVBox.__init__(self)
        self.frequencies = [20.0, 15.0]
        self.minimumScore = 0.25
        self.minimumMargin = 0.18
        self.calibratedMinimumScore = 2.0
        self.calibratedMinimumMargin = 1.0
        self.smoothingWindows = 2
        self.confirmationWindows = 2
        self.neutralUnlockWindows = 3
        self.bridgeUrl = "http://127.0.0.1:8766"
        self.signalHeader = None
        self.scoreHistory = []
        self.candidate = -1
        self.candidateCount = 0
        self.neutralCount = 0
        self.armed = True
        self.lastState = None
        self.lastHeartbeat = -1e9
        self.lastCalibrationPoll = -1e9
        self.calibrationPhase = "idle"
        self.calibrationSamples = {"neutral": [], "left": [], "right": []}
        self.calibrationBaseline = None
        self.calibrationScale = None
        self.calibrated = False
        self.codeByState = {}
        self.signalInfoPrinted = False

    def settingValue(self, name, fallback):
        try:
            return self.setting[name]
        except Exception:
            return fallback

    def initialize(self):
        frequencyText = str(self.settingValue("Frequencies (Hz)", "20.0,15.0"))
        self.frequencies = [float(value.strip()) for value in frequencyText.split(",") if value.strip()]
        if len(self.frequencies) != 2:
            raise ValueError("Frequencies (Hz) must contain exactly two values: left,right")
        self.minimumScore = float(self.settingValue("Minimum CCA score", 0.25))
        self.minimumMargin = float(self.settingValue("Minimum score margin", 0.18))
        self.calibratedMinimumScore = float(self.settingValue("Calibrated minimum z score", 2.0))
        self.calibratedMinimumMargin = float(self.settingValue("Calibrated minimum z margin", 1.0))
        self.smoothingWindows = max(1, int(self.settingValue("Smoothing windows", 2)))
        self.confirmationWindows = max(1, int(self.settingValue("Confirmation windows", 2)))
        self.neutralUnlockWindows = max(1, int(self.settingValue("Neutral unlock windows", 3)))
        self.bridgeUrl = str(self.settingValue("Bridge URL", "http://127.0.0.1:8766")).rstrip("/")
        self.codeByState[-1] = OpenViBE_stimulation["OVTK_StimulationId_Label_00"]
        self.codeByState[0] = OpenViBE_stimulation["OVTK_StimulationId_Label_01"]
        self.codeByState[1] = OpenViBE_stimulation["OVTK_StimulationId_Label_02"]
        self.output[0].append(OVStimulationHeader(0., 0.))
        self.sendToBridge("/openvibe/heartbeat")

    def scoreMargin(self, scores):
        ordered = sorted(scores, reverse=True)
        return (ordered[0] - ordered[1]) / max(ordered[0], 1e-12)

    def emitState(self, state, date, scores):
        code = self.codeByState[state]
        stimSet = OVStimulationSet(date, date + 1.0 / max(self.getClock(), 1.0))
        stimSet.append(OVStimulation(code, date, 0.))
        self.output[0].append(stimSet)
        label = "待机" if state < 0 else "左转" if state == 0 else "右转"
        print("SSVEP_STATE:", label, "scores =", [round(value, 3) for value in scores])

    def sendToBridge(self, endpoint, command=None, confidence=0.0, scores=None):
        values = {"confidence": str(max(0.0, min(1.0, float(confidence))))}
        if command:
            values["command"] = command
        if scores is not None:
            values["scores"] = json.dumps(scores, ensure_ascii=False)
        url = self.bridgeUrl + endpoint + "?" + urllib.parse.urlencode(values)
        try:
            with urllib.request.urlopen(url, timeout=0.25) as response:
                response.read()
            return True
        except Exception as error:
            print("OPENVIBE_BRIDGE_ERROR:", str(error))
            return False

    def heartbeat(self):
        now = time.monotonic()
        if now - self.lastHeartbeat >= 1.0:
            self.sendToBridge("/openvibe/heartbeat")
            self.lastHeartbeat = now

    def getCalibrationState(self):
        now = time.monotonic()
        if now - self.lastCalibrationPoll < 0.20:
            return {"phase": self.calibrationPhase, "active": self.calibrationPhase in ("neutral", "left", "right"), "complete": self.calibrated}
        self.lastCalibrationPoll = now
        try:
            with urllib.request.urlopen(self.bridgeUrl + "/calibration/state", timeout=0.25) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            return {"phase": self.calibrationPhase, "active": self.calibrationPhase in ("neutral", "left", "right"), "complete": self.calibrated}

    def finishCalibration(self):
        if any(len(self.calibrationSamples[name]) < 5 for name in ("neutral", "left", "right")):
            print("CALIBRATION_ERROR: each phase needs at least five EEG epochs")
            return
        neutral = numpy.asarray(self.calibrationSamples["neutral"], dtype=float)
        left = numpy.asarray(self.calibrationSamples["left"], dtype=float)
        right = numpy.asarray(self.calibrationSamples["right"], dtype=float)
        baseline = numpy.median(neutral, axis=0)
        scale = 1.4826 * numpy.median(numpy.abs(neutral - baseline), axis=0)
        scale = numpy.maximum(scale, 0.025)
        self.calibrationBaseline = baseline
        self.calibrationScale = scale
        self.calibrated = True
        leftZ = (numpy.median(left, axis=0) - baseline) / scale
        rightZ = (numpy.median(right, axis=0) - baseline) / scale
        print("CALIBRATION_DONE: baseline =", [round(value, 3) for value in baseline],
              "left_z =", [round(value, 2) for value in leftZ],
              "right_z =", [round(value, 2) for value in rightZ])

    def handleCalibration(self, state, rawScores):
        phase = state.get("phase", "idle")
        if phase == "neutral" and (self.calibrationPhase != "neutral" or float(state.get("elapsed", 1.0)) < 0.3):
            self.calibrationSamples = {"neutral": [], "left": [], "right": []}
            self.calibrated = False
            self.scoreHistory = []
            print("CALIBRATION_PHASE: neutral")
        elif phase != self.calibrationPhase and phase in ("left", "right"):
            self.scoreHistory = []
            print("CALIBRATION_PHASE:", phase)
        self.calibrationPhase = phase
        if phase in self.calibrationSamples:
            self.calibrationSamples[phase].append(rawScores)
            return True
        if state.get("complete") and not self.calibrated:
            self.finishCalibration()
            self.scoreHistory = []
            self.candidate = -1
            self.candidateCount = 0
            self.neutralCount = self.neutralUnlockWindows
            self.armed = True
            return True
        return False

    def decisionScores(self, rawScores):
        if self.calibrated and self.calibrationBaseline is not None and self.calibrationScale is not None:
            return [(rawScores[index] - self.calibrationBaseline[index]) / self.calibrationScale[index] for index in range(2)]
        return rawScores

    def bandpass(self, samplesByChannel, samplingRate, lowFrequency, highFrequency):
        numberOfSamples = samplesByChannel.shape[0]
        nfft = max(4096, 4 * numberOfSamples)
        spectrum = numpy.fft.rfft(samplesByChannel, n=nfft, axis=0)
        frequencyAxis = numpy.fft.rfftfreq(nfft, 1.0 / samplingRate)
        mask = (frequencyAxis >= lowFrequency) & (frequencyAxis <= highFrequency)
        filtered = numpy.fft.irfft(spectrum * mask[:, None], n=nfft, axis=0)
        return filtered[:numberOfSamples, :]

    def ccaCorrelation(self, samplesByChannel, reference):
        x = samplesByChannel - numpy.mean(samplesByChannel, axis=0, keepdims=True)
        y = reference - numpy.mean(reference, axis=0, keepdims=True)
        if x.shape[1] == 0 or y.shape[1] == 0:
            return 0.0
        denominator = max(x.shape[0] - 1, 1)
        covXX = numpy.dot(x.T, x) / denominator
        covYY = numpy.dot(y.T, y) / denominator
        covXY = numpy.dot(x.T, y) / denominator
        covXX += max(float(numpy.trace(covXX)), 1.0) * 1e-6 * numpy.eye(covXX.shape[0])
        covYY += max(float(numpy.trace(covYY)), 1.0) * 1e-6 * numpy.eye(covYY.shape[0])
        valuesX, vectorsX = numpy.linalg.eigh(covXX)
        valuesY, vectorsY = numpy.linalg.eigh(covYY)
        inverseSqrtX = numpy.dot(vectorsX * (1.0 / numpy.sqrt(numpy.maximum(valuesX, 1e-12))), vectorsX.T)
        inverseSqrtY = numpy.dot(vectorsY * (1.0 / numpy.sqrt(numpy.maximum(valuesY, 1e-12))), vectorsY.T)
        whitened = numpy.dot(numpy.dot(inverseSqrtX, covXY), inverseSqrtY)
        correlations = numpy.linalg.svd(whitened, compute_uv=False)
        return float(min(max(correlations[0], 0.0), 1.0)) if len(correlations) else 0.0

    def scoreAtFrequency(self, samplesByChannel, samplingRate, targetFrequency):
        numberOfSamples = samplesByChannel.shape[0]
        timeAxis = numpy.arange(numberOfSamples, dtype=float) / samplingRate
        score = 0.0
        for harmonic, weight in ((1, 0.70), (2, 0.20), (3, 0.10)):
            frequency = targetFrequency * harmonic
            if frequency >= samplingRate / 2.0 - 0.5 or frequency > 32.0:
                continue
            reference = numpy.column_stack((
                numpy.sin(2.0 * numpy.pi * frequency * timeAxis),
                numpy.cos(2.0 * numpy.pi * frequency * timeAxis),
            ))
            filtered = self.bandpass(
                samplesByChannel,
                samplingRate,
                max(0.5, frequency - 0.75),
                min(samplingRate / 2.0 - 0.5, frequency + 0.75),
            )
            rho = self.ccaCorrelation(filtered, reference)
            score += weight * rho * rho
        return float(score)

    def process(self):
        for chunkIdx in range(len(self.input[0])):
            chunk = self.input[0].pop()

            if type(chunk) == OVSignalHeader:
                self.signalHeader = chunk
                continue

            if type(chunk) == OVSignalBuffer:
                if self.signalHeader is None:
                    continue
                data = numpy.array(chunk, dtype=float).reshape(tuple(self.signalHeader.dimensionSizes))
                if data.ndim == 1:
                    data = data.reshape(1, -1)
                samplesByChannel = data.T
                samplingRate = float(self.signalHeader.samplingRate)
                if not self.signalInfoPrinted:
                    print("SIGNAL_INFO: sampling_rate =", samplingRate,
                          "channels =", data.shape[0], "samples_per_epoch =", data.shape[1])
                    self.signalInfoPrinted = True

                self.heartbeat()
                rawScores = [self.scoreAtFrequency(samplesByChannel, samplingRate, frequency)
                             for frequency in self.frequencies]
                if self.handleCalibration(self.getCalibrationState(), rawScores):
                    continue
                scoresForDecision = self.decisionScores(rawScores)
                self.scoreHistory.append(scoresForDecision)
                if len(self.scoreHistory) > self.smoothingWindows:
                    self.scoreHistory.pop(0)
                scores = [float(numpy.median([history[index] for history in self.scoreHistory]))
                          for index in range(2)]
                bestScore = max(scores)
                if self.calibrated:
                    ordered = sorted(scores, reverse=True)
                    margin = ordered[0] - ordered[1]
                    minimumScore = self.calibratedMinimumScore
                    minimumMargin = self.calibratedMinimumMargin
                else:
                    margin = self.scoreMargin(scores)
                    minimumScore = self.minimumScore
                    minimumMargin = self.minimumMargin
                if bestScore < minimumScore or margin < minimumMargin:
                    candidate = -1
                else:
                    candidate = int(numpy.argmax(scores))
                print("CCA_SCORE: left20 =", round(scores[0], 3),
                      "right15 =", round(scores[1], 3),
                      "raw =", [round(value, 3) for value in rawScores],
                      "margin =", round(margin, 3),
                      "candidate =", "none" if candidate < 0 else candidate)

                if self.lastState is None:
                    self.lastState = -1
                    self.emitState(-1, chunk.startTime, scores)
                if candidate < 0:
                    self.neutralCount += 1
                    if self.neutralCount >= self.neutralUnlockWindows:
                        self.armed = True
                else:
                    self.neutralCount = 0
                if candidate == self.candidate:
                    self.candidateCount += 1
                else:
                    self.candidate = candidate
                    self.candidateCount = 1
                if candidate >= 0 and self.candidateCount >= self.confirmationWindows and self.armed:
                    command = "left" if candidate == 0 else "right"
                    self.emitState(candidate, chunk.startTime, scores)
                    self.lastState = candidate
                    if self.sendToBridge("/openvibe/command", command, margin, {
                            "left20": round(scores[0], 4), "right15": round(scores[1], 4)}):
                        print("COMMAND_ACCEPTED:", command)
                    self.armed = False

            elif type(chunk) == OVSignalEnd:
                self.output[0].append(OVStimulationEnd(chunk.endTime, chunk.endTime))


box = MyOVBox()
