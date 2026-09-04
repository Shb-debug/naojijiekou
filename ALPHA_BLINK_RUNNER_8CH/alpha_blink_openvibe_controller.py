"""OpenViBE Python 3 Box: 8-channel alpha telemetry and blink events.

The 8-channel scenario supplies channels in this order by default:
two frontal blink channels first, followed by six posterior alpha channels.
The exact electrode names are selected in SUPER-mario.xml, so the same box
also works with an 8-channel device whose hardware channel numbering differs.

Blink detection is deliberately conservative. It uses a short trailing
window, edge-safe smoothing, robust adaptive noise estimates, two-window
confirmation, and a recovery-based re-arm state machine. The bridge is only
an OpenViBE event receiver; there is no second LSL detector in this path.
"""

import time
import urllib.parse
import urllib.request

import numpy


class MyOVBox(OVBox):
    def __init__(self):
        OVBox.__init__(self)
        self.header = None
        self.bridge = "http://127.0.0.1:8766"
        self.expectedChannels = 8
        self.frontalChannelCount = 2

        # Defaults are intentionally conservative for the supplied Fp1/Fp2
        # recordings. XML settings, when present, override these values.
        # Keep the analysis window short enough for responsive control. The
        # shape checks below still protect against isolated noise samples.
        self.blinkWindow = 0.32
        self.absoluteThreshold = 35.0
        self.zThreshold = 5.0
        self.releaseZ = 1.8
        self.refractory = 0.85
        self.warmupSeconds = 5.0
        self.calibrationWindowSeconds = 3.0
        self.minPeakRatio = 2.2
        self.confirmations = 2
        # Only exceptionally strong, already-recovered events may bypass the
        # normal two-window confirmation.
        self.immediateZThreshold = 12.0
        self.smoothingSeconds = 0.025
        self.negativePolarityRatio = 1.8
        self.maxPreNoiseRatio = 0.30
        self.minAgreement = 0.45
        self.minCorrelation = 0.75
        self.maxPeakLagSeconds = 0.12
        self.minRecoveryRatio = 0.30
        self.confirmationTimeout = 0.50
        self.band = (8.0, 13.0)

        self.calibration = []
        self.state = "warmup"
        self.candidateCount = 0
        self.candidateStarted = -1e9
        self.releaseCount = 0
        self.lastBlink = -1e9
        self.lastHeartbeat = -1e9
        self.lastCandidateLog = -1e9
        self.startedAt = None
        self.baseline = None
        self.baselineMad = 1.0
        self.lastPeakOffsetSeconds = 0.0
        self.lastOutputEndTime = 0.0

    def settingFloat(self, name, default):
        try:
            return float(self.setting.get(name, default))
        except Exception:
            return float(default)

    def initialize(self):
        self.bridge = str(self.setting.get("Bridge URL", self.bridge)).rstrip("/")
        self.expectedChannels = max(4, int(self.settingFloat("Expected channel count", self.expectedChannels)))
        self.frontalChannelCount = max(2, int(self.settingFloat("Frontal channel count", self.frontalChannelCount)))
        self.absoluteThreshold = self.settingFloat("Blink absolute threshold", self.absoluteThreshold)
        self.zThreshold = self.settingFloat("Blink z threshold", self.zThreshold)
        self.releaseZ = self.settingFloat("Blink release z", self.releaseZ)
        self.refractory = self.settingFloat("Blink refractory seconds", self.refractory)
        self.blinkWindow = max(.25, self.settingFloat("Blink detection window", self.blinkWindow))
        self.warmupSeconds = self.settingFloat("Blink warm-up seconds", self.warmupSeconds)
        self.calibrationWindowSeconds = self.settingFloat("Blink calibration window seconds", self.calibrationWindowSeconds)
        self.minPeakRatio = self.settingFloat("Blink minimum peak ratio", self.minPeakRatio)
        self.confirmations = max(2, int(self.settingFloat("Blink confirmation windows", self.confirmations)))
        self.immediateZThreshold = max(self.zThreshold + 2.0, self.settingFloat("Blink immediate z threshold", self.immediateZThreshold))
        self.smoothingSeconds = self.settingFloat("Blink smoothing seconds", self.smoothingSeconds)
        self.negativePolarityRatio = self.settingFloat("Blink negative polarity ratio", self.negativePolarityRatio)
        self.maxPreNoiseRatio = self.settingFloat("Blink maximum pre-noise ratio", self.maxPreNoiseRatio)
        self.minAgreement = self.settingFloat("Blink minimum channel agreement", self.minAgreement)
        self.minCorrelation = self.settingFloat("Blink minimum channel correlation", self.minCorrelation)
        self.maxPeakLagSeconds = self.settingFloat("Blink maximum peak lag seconds", self.maxPeakLagSeconds)
        self.minRecoveryRatio = self.settingFloat("Blink minimum recovery ratio", self.minRecoveryRatio)
        self.confirmationTimeout = self.settingFloat("Blink confirmation timeout seconds", self.confirmationTimeout)
        self.output[0].append(OVStimulationHeader(0., 0.))
        print("BLINK_MODE: OpenViBE-only conservative detector")
        print("CHANNEL_MODE: expected=%d frontal=%d posterior=all remaining channels" % (self.expectedChannels, self.frontalChannelCount))
        print("BLINK_CONFIG: window=%.2fs confirmations=%d immediate_z=%.1f refractory=%.2fs" % (self.blinkWindow, self.confirmations, self.immediateZThreshold, self.refractory))

    def send(self, endpoint, values=None):
        values = values or {}
        url = self.bridge + endpoint + ("?" + urllib.parse.urlencode(values) if values else "")
        try:
            with urllib.request.urlopen(url, timeout=0.08) as response:
                response.read()
        except Exception as error:
            print("ALPHA_BRIDGE_ERROR:", str(error))

    def alphaRatio(self, data, sampleRate):
        if data.shape[0] < max(32, int(sampleRate * .45)):
            return 0.0
        centered = data - numpy.median(data, axis=0, keepdims=True)
        nfft = max(256, 1 << int(numpy.ceil(numpy.log2(centered.shape[0]))))
        power = numpy.abs(numpy.fft.rfft(centered * numpy.hanning(centered.shape[0])[:, None], n=nfft, axis=0)) ** 2
        frequencies = numpy.fft.rfftfreq(nfft, 1.0 / sampleRate)
        total = (frequencies >= 4.) & (frequencies <= 30.)
        alpha = (frequencies >= self.band[0]) & (frequencies <= self.band[1])
        return float(numpy.sum(power[alpha]) / max(numpy.sum(power[total]), 1e-12))

    def blinkFeatures(self, data, sampleRate):
        """Extract shape features without zero-padding the moving average."""
        if data.ndim == 1:
            data = data[:, None]
        if data.shape[1] < 2:
            return None

        windowSize = max(64, int(round(sampleRate * self.blinkWindow)))
        data = numpy.asarray(data[-windowSize:, :2], dtype=float)
        if data.shape[0] < max(64, int(sampleRate * .25)):
            return None

        centered = data - numpy.median(data, axis=0, keepdims=True)
        smoothSize = max(5, int(round(sampleRate * self.smoothingSeconds)))
        if smoothSize % 2 == 0:
            smoothSize += 1
        pad = smoothSize // 2
        kernel = numpy.ones(smoothSize, dtype=float) / smoothSize
        smoothed = numpy.column_stack([
            numpy.convolve(numpy.pad(centered[:, i], (pad, pad), mode="edge"), kernel, mode="valid")
            for i in range(2)
        ])

        preCount = max(16, min(int(round(sampleRate * .12)), data.shape[0] // 3))
        channelBaseline = numpy.median(smoothed[:preCount], axis=0)
        smoothed -= channelBaseline
        common = numpy.median(smoothed, axis=1)

        channelPeaks = numpy.maximum(0.0, -numpy.min(smoothed, axis=0))
        peak = float(numpy.median(channelPeaks))
        agreement = float(numpy.min(channelPeaks) / max(float(numpy.max(channelPeaks)), 1e-9))
        if numpy.std(smoothed[:, 0]) > 1e-9 and numpy.std(smoothed[:, 1]) > 1e-9:
            correlation = float(numpy.corrcoef(smoothed.T)[0, 1])
        else:
            correlation = 0.0

        peakIndex = int(numpy.argmin(common))
        self.lastPeakOffsetSeconds = float(peakIndex / max(sampleRate, 1.0))
        negativePeak = max(0.0, float(-numpy.min(common)))
        positivePeak = max(0.0, float(numpy.max(common)))
        peakPosition = float(peakIndex / max(len(common) - 1, 1))

        pre = common[:preCount]
        preNoise = max(1.0, 1.4826 * float(numpy.median(numpy.abs(pre - numpy.median(pre)))))
        preNoiseRatio = float(preNoise / max(negativePeak, 1e-9))
        compactness = float(numpy.mean(common <= -max(negativePeak * .35, 1e-9)))
        polarityRatio = float(negativePeak / max(positivePeak, 1e-9))

        tailCount = max(16, int(round(sampleRate * .06)))
        tailLevel = float(numpy.median(common[-tailCount:]))
        recoveryRatio = float((tailLevel - float(numpy.min(common))) / max(negativePeak, 1e-9))

        lag = abs(int(numpy.argmin(smoothed[:, 0])) - int(numpy.argmin(smoothed[:, 1]))) / max(sampleRate, 1.0)
        return {
            "peak": peak,
            "agreement": agreement,
            "correlation": correlation,
            "compactness": compactness,
            "peakPosition": peakPosition,
            "polarityRatio": polarityRatio,
            "preNoiseRatio": preNoiseRatio,
            "recoveryRatio": recoveryRatio,
            "peakLagSeconds": float(lag),
        }

    def refreshBaseline(self, values):
        values = numpy.asarray(values, dtype=float)
        if values.size < 8:
            return False
        stable = values[values <= numpy.percentile(values, 75.0)]
        if stable.size < 4:
            stable = values
        self.baseline = float(numpy.percentile(stable, 35.0))
        self.baselineMad = max(1.0, 1.4826 * float(numpy.median(numpy.abs(stable - self.baseline))))
        return True

    def blink(self, data, now, sampleRate):
        if self.startedAt is None:
            self.startedAt = now

        features = self.blinkFeatures(data, sampleRate)
        if features is None:
            return False, 0.0, 0.0
        current = features["peak"]
        elapsed = now - self.startedAt

        if elapsed < self.warmupSeconds:
            self.calibration.append(current)
            self.calibration = self.calibration[-80:]
            return False, 0.0, current

        if self.baseline is None:
            count = max(8, int(round(self.calibrationWindowSeconds / .10)))
            if not self.refreshBaseline(self.calibration[-count:]):
                return False, 0.0, current
            self.state = "armed"
            print("BLINK_CALIBRATED: baseline=%.2f mad=%.2f" % (self.baseline, self.baselineMad))

        baseline = self.baseline
        mad = self.baselineMad
        z = (current - baseline) / max(mad, 1e-9)
        threshold = max(self.absoluteThreshold, baseline * self.minPeakRatio, baseline + self.zThreshold * mad)
        confidence = max(0.0, min(1.0, .35 + .10 * max(0.0, z - self.zThreshold) + .25 * min(features["recoveryRatio"], 1.0)))

        candidate = (
            current >= threshold
            and z >= self.zThreshold
            and features["agreement"] >= self.minAgreement
            and features["correlation"] >= self.minCorrelation
            and .20 <= features["peakPosition"] <= .98
            and .08 <= features["compactness"] <= .75
            and features["polarityRatio"] >= self.negativePolarityRatio
            and features["preNoiseRatio"] <= self.maxPreNoiseRatio
            and features["peakLagSeconds"] <= self.maxPeakLagSeconds
        )
        recovered = (
            features["recoveryRatio"] >= self.minRecoveryRatio
            and features["peakPosition"] <= .90
        )

        if self.state == "latched":
            released = z <= self.releaseZ and current <= baseline + self.releaseZ * mad
            self.releaseCount = self.releaseCount + 1 if released else 0
            if self.releaseCount >= 2 and now - self.lastBlink >= self.refractory:
                self.state = "armed"
                self.releaseCount = 0
            return False, confidence, z

        if self.state == "armed":
            if candidate:
                # A very strong, synchronous and already-recovered waveform
                # is safe to accept immediately. Normal-strength candidates
                # still require the existing two-window confirmation.
                immediate = (
                    recovered
                    and z >= self.immediateZThreshold
                    and features["agreement"] >= max(self.minAgreement, .65)
                    and features["correlation"] >= max(self.minCorrelation, .90)
                )
                if immediate and now - self.lastBlink >= self.refractory:
                    self.state = "latched"
                    self.lastBlink = now
                    self.candidateCount = 0
                    print("BLINK_FAST_CANDIDATE: peak=%.2f z=%.2f recovery=%.2f" % (current, z, features["recoveryRatio"]))
                    return True, confidence, z
                self.state = "candidate"
                self.candidateCount = 1
                self.candidateStarted = now
                if now - self.lastCandidateLog >= .5:
                    print("BLINK_CANDIDATE: peak=%.2f z=%.2f recovery=%.2f" % (current, z, features["recoveryRatio"]))
                    self.lastCandidateLog = now
            else:
                # Track only stable windows. A large unexplained excursion must
                # not raise the baseline and hide the next real blink.
                if z < 3.0:
                    self.calibration.append(current)
                    self.calibration = self.calibration[-80:]
                    self.refreshBaseline(self.calibration[-60:])
            return False, confidence, z

        if self.state == "candidate":
            if now - self.candidateStarted > self.confirmationTimeout:
                self.state = "armed"
                self.candidateCount = 0
                return False, confidence, z
            if candidate:
                self.candidateCount += 1
            if recovered and self.candidateCount >= self.confirmations and now - self.lastBlink >= self.refractory:
                self.state = "latched"
                self.lastBlink = now
                self.candidateCount = 0
                return True, confidence, z
            return False, confidence, z

        self.state = "armed"
        return False, confidence, z

    def process(self):
        for _ in range(len(self.input[0])):
            chunk = self.input[0].pop()
            if type(chunk) == OVSignalHeader:
                self.header = chunk
            elif type(chunk) == OVSignalBuffer and self.header is not None:
                values = numpy.array(chunk, dtype=float).reshape(tuple(self.header.dimensionSizes))
                if values.ndim == 1:
                    values = values.reshape(1, -1)
                channels = values.T
                sampleRate = float(self.header.samplingRate)
                minimumChannels = max(self.expectedChannels, self.frontalChannelCount + 2)
                if channels.shape[1] < minimumChannels:
                    print("CHANNEL_MAPPING_ERROR: expected at least %d channels; received %d" % (minimumChannels, channels.shape[1]))
                    continue

                frontal = channels[:, :self.frontalChannelCount]
                posterior = channels[:, self.frontalChannelCount:]
                now = time.monotonic()
                ratio = self.alphaRatio(posterior, sampleRate)
                event, confidence, score = self.blink(frontal, now, sampleRate)

                if now - self.lastHeartbeat >= .5:
                    self.send("/openvibe/alpha", {
                        "alpha_ratio": round(ratio, 5),
                        "blink_confidence": round(confidence, 4),
                        "blink_score": round(score, 3),
                        "blink_state": self.state,
                    })
                    self.send("/openvibe/heartbeat")
                    self.lastHeartbeat = now

                # Epochs overlap, so their own [start, end] dates cannot be
                # copied directly to a stimulation stream that must advance
                # continuously. Advance a separate non-overlapping cursor.
                outputStart = self.lastOutputEndTime
                outputEnd = max(outputStart, float(chunk.endTime))
                stim = OVStimulationSet(outputStart, outputEnd)
                if event:
                    # The peak may be inside an older overlapping epoch. A
                    # historical stimulation date would be late for the
                    # listener, so publish it at the current available end.
                    eventTime = outputEnd
                    stim.append(OVStimulation(OpenViBE_stimulation["OVTK_StimulationId_Label_01"], eventTime, 0.))
                    print("BLINK_JUMP: confidence=%.2f z=%.2f peak_offset=%.3fs" % (confidence, score, self.lastPeakOffsetSeconds))
                self.lastOutputEndTime = outputEnd
                self.output[0].append(stim)
                if event:
                    self.send("/openvibe/blink", {
                        "confidence": round(confidence, 4),
                        "blink_score": round(score, 3),
                        "alpha_ratio": round(ratio, 5),
                    })
            elif type(chunk) == OVSignalEnd:
                self.output[0].append(OVStimulationEnd(chunk.endTime, chunk.endTime))


box = MyOVBox()
