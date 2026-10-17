// dervo-voice-audio: the microphone and speaker for Dervo's voice process (backend/dervo_voice/audio.py).
//
// AVAudioEngine with Apple's voice processing on: echo cancellation, so the microphone signal arrives with Dervo's own
// voice taken out (that's what makes barge-in work on speakers), plus noise suppression. Other audio isn't ducked.
//
//   stdout  16 kHz mono int16 little-endian, 512 samples per frame, nothing else.
//   stdin   playback packets: "DVPA" + uint32 sample rate + uint32 byte count + int16 samples.
//           Packets with no samples are controls, by rate: 0 stop now and drop anything queued, 1 close the microphone
//           (its engine stops, so macOS shows the mic as off), 2 open it. It starts closed.
//   stderr  one JSON object per line: {"audio":"ready"|"denied"|"lost"|"changed"|"closed"|"error","detail":...}
//
// It exits when stdin closes (the voice process went away). Build: voice/mac/build.sh. Not built or run in the cloud
// session that wrote it (no macOS there); see the PR for what was and wasn't checked.
import AVFoundation
import Foundation

let frameSamples = 512
let micFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!
let speakFormat = AVAudioFormat(standardFormatWithSampleRate: 24000, channels: 1)!
let stdout = FileHandle.standardOutput
let stderrLock = NSLock()
// Frames are written off the audio thread. If the voice process falls behind by more than about 2 s, frames are dropped
// rather than blocking the tap callback (a full pipe would otherwise stall Core Audio).
let writeQueue = DispatchQueue(label: "dervo.voice.out")
let maxInFlight = 64

func say(_ fields: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: fields), var line = String(data: data, encoding: .utf8) else { return }
    line += "\n"
    stderrLock.lock(); FileHandle.standardError.write(line.data(using: .utf8)!); stderrLock.unlock()
}

final class Audio {
    // Two engines. `engine` is the microphone, with voice processing, and plays Dervo's voice while it's open so the
    // echo canceller hears what to take out. `speaker` plays only and never touches an input node, so Dervo can still
    // talk while the microphone is closed (hold to talk between holds, or muted) and macOS shows the mic as off.
    let engine = AVAudioEngine()
    let player = AVAudioPlayerNode()
    let speaker = AVAudioEngine()
    let speakerPlayer = AVAudioPlayerNode()
    var converter: AVAudioConverter?
    var pending = [Int16]()
    let lock = NSLock()
    var running = false
    var wanted = false  // the voice process opens the mic (control packet 2) only when something listens
    var inFlight = 0

    func start() {
        guard wanted, !running else { return }
        do {
            let input = engine.inputNode
            try input.setVoiceProcessingEnabled(true)
            if #available(macOS 14.0, *) {
                input.voiceProcessingOtherAudioDuckingConfiguration = .init(enableAdvancedDucking: false, duckingLevel: .min)
            }
            if engine.attachedNodes.contains(player) == false { engine.attach(player) }
            engine.connect(player, to: engine.mainMixerNode, format: speakFormat)
            let inputFormat = input.outputFormat(forBus: 0)
            guard inputFormat.sampleRate > 0 else { say(["audio": "lost", "detail": "No input device."]); return }
            converter = AVAudioConverter(from: inputFormat, to: micFormat)
            input.removeTap(onBus: 0)
            input.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in self?.heard(buffer) }
            engine.prepare()
            try engine.start()
            player.play()
            speakerPlayer.stop()  // anything still playing on the speak-only engine would bypass the echo canceller
            running = true
            say(["audio": "ready", "rate": inputFormat.sampleRate, "device": AVCaptureDevice.default(for: .audio)?.localizedName ?? ""])
        } catch {
            running = false
            say(["audio": "lost", "detail": error.localizedDescription])
        }
    }

    func stopCapture() {
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        lock.lock(); pending.removeAll(); lock.unlock()
        running = false
    }

    func capture(_ on: Bool) {
        wanted = on
        if on {
            if AVCaptureDevice.authorizationStatus(for: .audio) == .authorized { start() } else { askForMic() }
        } else if running {
            stopCapture()
            say(["audio": "closed"])
        }
    }

    func restart() {
        guard wanted else { return }
        stopCapture()
        start()
        say(["audio": "changed"])
    }

    /// Microphone audio to 16 kHz int16, written out in whole 512-sample frames.
    func heard(_ buffer: AVAudioPCMBuffer) {
        guard let converter else { return }
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * micFormat.sampleRate / buffer.format.sampleRate) + 32
        guard let out = AVAudioPCMBuffer(pcmFormat: micFormat, frameCapacity: capacity) else { return }
        var given = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if given { status.pointee = .noDataNow; return nil }
            given = true; status.pointee = .haveData; return buffer
        }
        guard error == nil, let samples = out.int16ChannelData?[0] else { return }
        lock.lock()
        pending.append(contentsOf: UnsafeBufferPointer(start: samples, count: Int(out.frameLength)))
        var chunks = [Data]()
        while pending.count >= frameSamples {
            chunks.append(pending.prefix(frameSamples).withUnsafeBufferPointer { Data(buffer: $0) })
            pending.removeFirst(frameSamples)
        }
        lock.unlock()
        for chunk in chunks {
            lock.lock()
            let full = inFlight >= maxInFlight
            if !full { inFlight += 1 }
            lock.unlock()
            if full { continue }
            writeQueue.async {
                stdout.write(chunk)
                self.lock.lock(); self.inFlight -= 1; self.lock.unlock()
            }
        }
    }

    func startSpeaker() -> Bool {
        if speaker.isRunning { return true }
        if speaker.attachedNodes.contains(speakerPlayer) == false { speaker.attach(speakerPlayer) }
        speaker.connect(speakerPlayer, to: speaker.mainMixerNode, format: speakFormat)
        do { speaker.prepare(); try speaker.start(); return true } catch {
            say(["audio": "error", "detail": "Couldn't start the speaker: \(error.localizedDescription)"])
            return false
        }
    }

    /// Dervo's voice. Any rate in, played at the player's 24 kHz float.
    func play(rate: Double, samples: Data) {
        guard rate > 0, samples.count >= 2 else { return }
        let target: AVAudioPlayerNode
        if running { target = player } else if startSpeaker() { target = speakerPlayer } else { return }
        let count = samples.count / 2
        guard let source = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1, interleaved: false),
              let incoming = AVAudioPCMBuffer(pcmFormat: source, frameCapacity: AVAudioFrameCount(count)) else { return }
        incoming.frameLength = AVAudioFrameCount(count)
        samples.withUnsafeBytes { raw in
            let ints = raw.bindMemory(to: Int16.self)
            for index in 0..<count { incoming.floatChannelData![0][index] = Float(ints[index]) / 32768 }
        }
        var buffer = incoming
        if rate != speakFormat.sampleRate, let convert = AVAudioConverter(from: source, to: speakFormat),
           let out = AVAudioPCMBuffer(pcmFormat: speakFormat, frameCapacity: AVAudioFrameCount(Double(count) * speakFormat.sampleRate / rate) + 32) {
            var given = false
            convert.convert(to: out, error: nil) { _, status in
                if given { status.pointee = .noDataNow; return nil }
                given = true; status.pointee = .haveData; return incoming
            }
            buffer = out
        }
        target.scheduleBuffer(buffer, completionHandler: nil)
        if !target.isPlaying { target.play() }
    }

    func stopSpeaking() {
        for node in [player, speakerPlayer] where node.engine?.isRunning == true {
            node.stop()
            node.play()
        }
    }
}

let audio = Audio()

func askForMic() {
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized:
        audio.start()
    case .notDetermined:
        AVCaptureDevice.requestAccess(for: .audio) { granted in
            DispatchQueue.main.async { if granted { audio.start() } else { say(["audio": "denied"]) } }
        }
    default:
        say(["audio": "denied"])
    }
}

func readExactly(_ count: Int) -> Data? {
    var data = Data()
    while data.count < count {
        let chunk = FileHandle.standardInput.readData(ofLength: count - data.count)
        if chunk.isEmpty { return nil }
        data.append(chunk)
    }
    return data
}

func readPackets() {
    while let header = readExactly(12) {
        guard header.prefix(4) == Data("DVPA".utf8) else { say(["audio": "error", "detail": "Bad playback packet."]); exit(2) }
        let rate = header.subdata(in: 4..<8).withUnsafeBytes { $0.load(as: UInt32.self).littleEndian }
        let size = header.subdata(in: 8..<12).withUnsafeBytes { $0.load(as: UInt32.self).littleEndian }
        if rate == 0 { DispatchQueue.main.async { audio.stopSpeaking() }; continue }
        if rate == 1 || rate == 2 { DispatchQueue.main.async { audio.capture(rate == 2) }; continue }
        guard let samples = readExactly(Int(size)) else { break }
        DispatchQueue.main.async { audio.play(rate: Double(rate), samples: samples) }
    }
    exit(0)  // the voice process closed our stdin
}

signal(SIGPIPE, SIG_IGN)
// The microphone stays closed until the voice process asks for it. Permission is asked for then, not at launch.
// AirPods connecting mid-sentence, a Zoom call taking the mic, sleep and wake: the engine's configuration changes and it
// stops. Start it again on the new device and say so; the voice process shows "changed".
NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: audio.engine, queue: .main) { _ in
    audio.restart()
}
NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: audio.speaker, queue: .main) { _ in
    audio.speaker.stop()  // started again on the next line Dervo says
}
Thread.detachNewThread { readPackets() }
// A lost device is retried every 2 s, quietly, while the mic is wanted.
Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { _ in
    if audio.wanted && !audio.running && AVCaptureDevice.authorizationStatus(for: .audio) == .authorized { audio.start() }
}
RunLoop.main.run()
