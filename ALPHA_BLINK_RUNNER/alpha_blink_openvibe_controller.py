"""OpenViBE Python 3 Box: Alpha readiness + low-latency blink events.

Input should contain the exact Channel Selector order:
Fp1/Fp2 (hardware channels 1/2), then O1/Oz/O2/Pz
(hardware channels 15/14/16/13). Each epoch is 0.45 s and arrives every 0.10 s.
"""
import json
import time
import urllib.parse
import urllib.request

import numpy


class MyOVBox(OVBox):
    def __init__(self):
        OVBox.__init__(self)
        self.header = None
        self.bridge = "http://127.0.0.1:8766"
        self.blinkWindow = 0.45
        self.alphaWindow = 0.90
        self.absoluteThreshold = 35.0
        self.zThreshold = 5.0
        self.releaseZ = 1.8
        self.refractory = 0.55
        self.band = (8.0, 13.0)
        self.history = []
        self.latched = False
        self.lastBlink = -1e9
        self.lastHeartbeat = -1e9

    def initialize(self):
        self.bridge = str(self.setting.get("Bridge URL", self.bridge)).rstrip("/")
        self.absoluteThreshold = float(self.setting.get("Blink absolute threshold", self.absoluteThreshold))
        self.zThreshold = float(self.setting.get("Blink z threshold", self.zThreshold))
        self.refractory = float(self.setting.get("Blink refractory seconds", self.refractory))
        self.output[0].append(OVStimulationHeader(0., 0.))

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

    def blink(self, data, now):
        centered = data - numpy.median(data, axis=0, keepdims=True)
        current = float(numpy.median(numpy.percentile(centered, 95, axis=0) - numpy.percentile(centered, 5, axis=0)))
        self.history.append(current)
        self.history = self.history[-45:]
        previous = numpy.asarray(self.history[:-1], dtype=float)
        if len(previous) < 8:
            return False, 0.0, current
        baseline = float(numpy.median(previous)); mad = max(1.4826 * float(numpy.median(numpy.abs(previous - baseline))), 1.0)
        z = (current - baseline) / mad
        threshold = max(self.absoluteThreshold, baseline + self.zThreshold * mad)
        confidence = max(0., min(1., (current - threshold) / max(threshold, 1.) + .55))
        detected = current >= threshold and z >= self.zThreshold
        if self.latched:
            if z <= self.releaseZ: self.latched = False
            detected = False
        if detected and now - self.lastBlink >= self.refractory:
            self.latched = True; self.lastBlink = now
            return True, confidence, z
        return False, confidence, z

    def process(self):
        for _ in range(len(self.input[0])):
            chunk = self.input[0].pop()
            if type(chunk) == OVSignalHeader:
                self.header = chunk
            elif type(chunk) == OVSignalBuffer and self.header is not None:
                values = numpy.array(chunk, dtype=float).reshape(tuple(self.header.dimensionSizes))
                if values.ndim == 1: values = values.reshape(1, -1)
                channels = values.T
                sampleRate = float(self.header.samplingRate)
                # OpenViBE Channel Selector order is Fp1, Fp2, O1, Oz, O2, Pz.
                # The selector uses hardware channels 1;2;15;14;16;13.
                if channels.shape[1] < 6:
                    print("CHANNEL_MAPPING_ERROR: expected 6 channels in order Fp1,Fp2,O1,Oz,O2,Pz")
                    continue
                frontal = channels[:, :2]
                posterior = channels[:, 2:6]
                now = time.monotonic()
                ratio = self.alphaRatio(posterior, sampleRate)
                event, confidence, score = self.blink(frontal, now)
                if now - self.lastHeartbeat >= .5:
                    self.send("/openvibe/alpha", {"alpha_ratio": round(ratio, 5)})
                    self.send("/openvibe/heartbeat")
                    self.lastHeartbeat = now
                if event:
                    stim = OVStimulationSet(chunk.startTime, chunk.startTime + 1. / max(self.getClock(), 1.))
                    stim.append(OVStimulation(OpenViBE_stimulation["OVTK_StimulationId_Label_01"], chunk.startTime, 0.))
                    self.output[0].append(stim)
                    self.send("/openvibe/blink", {"confidence": round(confidence, 4), "blink_score": round(score, 3), "alpha_ratio": round(ratio, 5)})
                    print("BLINK_JUMP:", round(confidence, 2), "alpha:", round(ratio, 3))
            elif type(chunk) == OVSignalEnd:
                self.output[0].append(OVStimulationEnd(chunk.endTime, chunk.endTime))


box = MyOVBox()
