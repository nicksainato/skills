import AppKit

// A small floating pill that shows whether dictation is listening.
//
// State arrives as one word in $STATE_DIR/indicator.state, written by
// whisperkit-dictate: idle, recording, transcribing, error:<message>, quit.
// The level meter reads the tail of the WAV that ffmpeg is still writing, so
// the bars show the audio that will actually be transcribed. A microphone
// with no grant records digital silence, and that reads as a flat line here
// instead of a surprise after speaking.

// MARK: - Paths

func resolveStateDir() -> URL {
    let args = CommandLine.arguments
    if let flag = args.firstIndex(of: "--state-dir"), flag + 1 < args.count {
        return URL(fileURLWithPath: args[flag + 1]).standardizedFileURL
    }
    if let dir = ProcessInfo.processInfo.environment["WHISPERKIT_DICTATE_DIR"], !dir.isEmpty {
        return URL(fileURLWithPath: dir).standardizedFileURL
    }
    return URL(fileURLWithPath: NSTemporaryDirectory())
        .appendingPathComponent("whisperkit-dictate").standardizedFileURL
}

let stateDir = resolveStateDir()
let stateFile = stateDir.appendingPathComponent("indicator.state")
let wavFile = stateDir.appendingPathComponent("recording.wav")
let pidFile = stateDir.appendingPathComponent("indicator.pid")

// MARK: - State

enum IndicatorState: Equatable {
    case idle
    case recording
    case transcribing
    case error(String)

    static func parse(_ raw: String) -> IndicatorState? {
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.isEmpty { return nil }
        if value.hasPrefix("recording:") { return .recording }
        if value.hasPrefix("error:") {
            let message = String(value.dropFirst("error:".count))
                .trimmingCharacters(in: .whitespaces)
            return .error(message.isEmpty ? "Dictation failed" : message)
        }
        switch value {
        case "idle": return .idle
        case "recording": return .recording
        case "transcribing": return .transcribing
        default: return nil
        }
    }

    var isVisible: Bool { self != .idle }
}

// MARK: - Recording clock

// whisperkit-dictate sends "recording:<limit>:<unix deadline>". The deadline
// is absolute, so the countdown stays right even if this app started late or
// was restarted while a recording was running.
struct RecordingClock {
    let limit: Double
    let deadline: Date

    var remaining: Double { max(0, deadline.timeIntervalSinceNow) }
    var elapsed: Double { max(0, limit - remaining) }
    var isEndingSoon: Bool { remaining <= 30 }

    static func parse(_ raw: String) -> RecordingClock? {
        let parts = raw.split(separator: ":")
        guard parts.count == 3, let limit = Double(parts[1]), let epoch = Double(parts[2])
        else { return nil }
        return RecordingClock(limit: limit, deadline: Date(timeIntervalSince1970: epoch))
    }

    static func format(_ seconds: Double) -> String {
        let total = Int(seconds.rounded())
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}

// MARK: - Placement

// Free dragging with a snap. Eight anchors are enough to put the pill out of
// the way on any screen, and snapping means it always sits square against an
// edge instead of wherever the mouse happened to let go.
enum Anchor: String, CaseIterable {
    case topLeft, topCenter, topRight
    case leftCenter, rightCenter
    case bottomLeft, bottomCenter, bottomRight

    static let margin: CGFloat = 12
    static let key = "anchor"

    // Against a side edge the pill stands on end. Lying flat there it would
    // jut into the middle of the screen, which is the one place it should
    // never be.
    var isVertical: Bool { self == .leftCenter || self == .rightCenter }

    func size(length: CGFloat, thickness: CGFloat) -> NSSize {
        isVertical
            ? NSSize(width: thickness, height: length)
            : NSSize(width: length, height: thickness)
    }

    static var saved: Anchor {
        get {
            guard let raw = UserDefaults.standard.string(forKey: key),
                let anchor = Anchor(rawValue: raw)
            else { return .bottomCenter }
            return anchor
        }
        set { UserDefaults.standard.set(newValue.rawValue, forKey: key) }
    }

    func origin(size: NSSize, in area: NSRect) -> NSPoint {
        let margin = Anchor.margin
        let x: CGFloat
        switch self {
        case .topLeft, .leftCenter, .bottomLeft: x = area.minX + margin
        case .topCenter, .bottomCenter: x = area.midX - size.width / 2
        case .topRight, .rightCenter, .bottomRight: x = area.maxX - size.width - margin
        }
        let y: CGFloat
        switch self {
        case .bottomLeft, .bottomCenter, .bottomRight: y = area.minY + margin
        case .leftCenter, .rightCenter: y = area.midY - size.height / 2
        case .topLeft, .topCenter, .topRight: y = area.maxY - size.height - margin
        }
        return NSPoint(x: x.rounded(), y: y.rounded())
    }

    static func nearest(to frame: NSRect, in area: NSRect) -> Anchor {
        let center = NSPoint(x: frame.midX, y: frame.midY)
        return allCases.min { a, b in
            distance(a, frame.size, area, center) < distance(b, frame.size, area, center)
        } ?? .bottomCenter
    }

    private static func distance(
        _ anchor: Anchor, _ size: NSSize, _ area: NSRect, _ point: NSPoint
    ) -> CGFloat {
        let origin = anchor.origin(size: size, in: area)
        let dx = origin.x + size.width / 2 - point.x
        let dy = origin.y + size.height / 2 - point.y
        return dx * dx + dy * dy
    }
}

// MARK: - Level meter

// Reads the newest samples from the growing WAV. ffmpeg writes it with
// -flush_packets, so the tail is current to within one packet.
//
// The decibel range comes from measuring 100 ms windows of real dictation on
// a MacBook Pro microphone: silence bottoms out near -64 dBFS, pauses between
// words sit around -44, ordinary speech runs -35 to -25, and the loudest
// syllables reach about -20. Mapping that span linearly in decibels is what
// makes the meter move; a wider range flattens it to a bar that is always
// most of the way up whether or not anyone is talking.
final class LevelMeter {
    static let floorDB = -50.0
    static let ceilingDB = -20.0

    private let url: URL
    private var handle: FileHandle?
    private var lastSize: UInt64 = 0
    private let windowBytes = 3200  // 100 ms of 16 kHz mono s16le

    init(url: URL) { self.url = url }

    // Each recording writes a new file, so the open handle is dropped with it.
    func reset() {
        try? handle?.close()
        handle = nil
        lastSize = 0
    }

    // nil means the file did not grow: the recorder stalled or died. That is
    // not the same as silence, which still writes samples.
    func sample() -> Double? {
        // Opened once per recording rather than on every frame: this runs 30
        // times a second for as long as someone is talking.
        if handle == nil {
            handle = try? FileHandle(forReadingFrom: url)
            lastSize = 0
        }
        guard let handle else { return nil }
        guard let end = try? handle.seekToEnd(), end > 44 else { return nil }
        if end <= lastSize { return nil }
        let fresh = Int(min(end - max(lastSize, 44), UInt64(windowBytes)))
        lastSize = end
        let length = fresh - (fresh % 2)
        guard length >= 2 else { return nil }
        try? handle.seek(toOffset: end - UInt64(length))
        guard let data = try? handle.read(upToCount: length), data.count >= 2 else { return nil }

        var sum = 0.0
        var count = 0
        // Decoded byte by byte. A Data slice is not guaranteed to be aligned
        // for Int16, and binding memory to a misaligned pointer is undefined.
        data.withUnsafeBytes { raw in
            var index = 0
            while index + 1 < raw.count {
                let bits = UInt16(raw[index]) | (UInt16(raw[index + 1]) << 8)
                let value = Double(Int16(bitPattern: bits)) / 32768.0
                sum += value * value
                count += 1
                index += 2
            }
        }
        guard count > 0 else { return 0 }
        let rms = (sum / Double(count)).squareRoot()
        guard rms > 0 else { return 0 }
        let db = 20 * log10(rms)
        let range = LevelMeter.ceilingDB - LevelMeter.floorDB
        return min(max((db - LevelMeter.floorDB) / range, 0), 1)
    }
}

// MARK: - Pill view

final class PillView: NSView {
    var state: IndicatorState = .idle
    var levels: [Double] = []
    var clock: RecordingClock?
    // True between the key press and the first captured sample. Opening the
    // microphone takes about 400 ms, and flat bars during that window look
    // exactly like the dead microphone this pill exists to reveal.
    var isWarming = false
    var orientation: Anchor = .bottomCenter
    var barCount = 18
    var onDragEnded: (() -> Void)?
    private(set) var isDragging = false
    private var grabOffset = NSSize(width: 0, height: 0)

    // Shared by the drawing and by length(for:), so the capsule is always
    // exactly as long as what is inside it.
    private let padding: CGFloat = 10
    private let dotSize: CGFloat = 6
    private let dotGap: CGFloat = 8
    private let barWidth: CGFloat = 2
    private let barGap: CGFloat = 3
    private let timerWidth: CGFloat = 32
    private let timerFont = NSFont.monospacedDigitSystemFont(ofSize: 10.5, weight: .medium)
    private let labelFont = NSFont.systemFont(ofSize: 11, weight: .medium)

    private var barSpan: CGFloat {
        CGFloat(barCount) * barWidth + CGFloat(barCount - 1) * barGap
    }

    // A live recording carries a clock; a hand-written "recording" token has
    // none, and then there is nothing to reserve room for.
    private var timerSpan: CGFloat { clock == nil ? 0 : timerWidth + 6 }

    override var isFlipped: Bool { false }

    // Everything is drawn in a flat left-to-right space of this size, then
    // rotated into place. One layout, four edges.
    private var content = NSRect.zero

    // The panel never becomes key, so without this the first click would be
    // spent activating instead of dragging.
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func mouseDown(with event: NSEvent) {
        guard let window else { return }
        let mouse = NSEvent.mouseLocation
        grabOffset = NSSize(
            width: mouse.x - window.frame.minX,
            height: mouse.y - window.frame.minY
        )
        isDragging = true
    }

    override func mouseDragged(with event: NSEvent) {
        guard isDragging, let window else { return }
        let mouse = NSEvent.mouseLocation
        window.setFrameOrigin(
            NSPoint(x: mouse.x - grabOffset.width, y: mouse.y - grabOffset.height))
    }

    override func mouseUp(with event: NSEvent) {
        guard isDragging else { return }
        isDragging = false
        onDragEnded?()
    }

    override func draw(_ dirtyRect: NSRect) {
        drawCapsule()
        guard state != .idle else { return }

        NSGraphicsContext.current?.saveGraphicsState()
        let transform = NSAffineTransform()
        if orientation.isVertical {
            content = NSRect(x: 0, y: 0, width: bounds.height, height: bounds.width)
            if orientation == .leftCenter {
                // Reads bottom to top, tilted away from the left edge.
                transform.translateX(by: bounds.width, yBy: 0)
                transform.rotate(byDegrees: 90)
            } else {
                transform.translateX(by: 0, yBy: bounds.height)
                transform.rotate(byDegrees: -90)
            }
        } else {
            content = bounds
        }
        transform.concat()

        switch state {
        case .recording: drawRecording()
        case .transcribing: drawTranscribing()
        case .error(let message): drawError(message)
        case .idle: break
        }
        NSGraphicsContext.current?.restoreGraphicsState()
    }

    // Solid, near black, fully rounded: it has to read at a glance against a
    // bright document, which a translucent grey panel does not.
    private func drawCapsule() {
        let radius = min(bounds.width, bounds.height) / 2
        NSColor(calibratedWhite: 0.02, alpha: 0.97).setFill()
        NSBezierPath(roundedRect: bounds, xRadius: radius, yRadius: radius).fill()
        let rim = NSBezierPath(
            roundedRect: bounds.insetBy(dx: 0.5, dy: 0.5),
            xRadius: radius - 0.5, yRadius: radius - 0.5)
        rim.lineWidth = 1
        NSColor(calibratedWhite: 1.0, alpha: 0.10).setStroke()
        rim.stroke()
    }

    private func drawRecording() {
        let time = CACurrentMediaTime()
        let pulse = 0.65 + 0.35 * (0.5 + 0.5 * sin(time * 3.4))
        let dotRect = NSRect(
            x: padding, y: content.midY - dotSize / 2, width: dotSize, height: dotSize)
        // Amber until audio is actually arriving, red once it is.
        let dot =
            isWarming
            ? NSColor(calibratedRed: 1.0, green: 0.72, blue: 0.24, alpha: pulse)
            : NSColor(calibratedRed: 1.0, green: 0.31, blue: 0.29, alpha: pulse)
        dot.setFill()
        NSBezierPath(ovalIn: dotRect).fill()

        var x = dotRect.maxX + dotGap
        let maxHeight = content.height - 10
        for index in 0..<barCount {
            let level = isWarming ? warmingLevel(index, time) : levelAt(index)
            let height = max(2, CGFloat(level) * maxHeight)
            let rect = NSRect(x: x, y: content.midY - height / 2, width: barWidth, height: height)
            // Newest bars are on the right and are the brightest.
            let age = Double(index) / Double(max(barCount - 1, 1))
            let alpha = 0.34 + 0.62 * age
            NSColor(calibratedWhite: 1.0, alpha: alpha).setFill()
            NSBezierPath(roundedRect: rect, xRadius: barWidth / 2, yRadius: barWidth / 2).fill()
            x += barWidth + barGap
        }
        drawTimer()
    }

    private func levelAt(_ index: Int) -> Double {
        index < levels.count ? levels[index] : 0
    }

    // A low ripple travelling left to right: clearly alive, clearly not yet
    // showing anyone's voice.
    private func warmingLevel(_ index: Int, _ time: Double) -> Double {
        let phase = Double(index) / Double(max(barCount - 1, 1)) * 2.6 - time * 4
        return 0.06 + 0.16 * (0.5 + 0.5 * sin(phase))
    }

    // Grey while there is room, red for the last 30 seconds. The recording
    // stops itself at the limit and is transcribed, so this is a warning, not
    // a deadline to beat.
    private func drawTimer() {
        guard let clock else { return }
        let ending = clock.isEndingSoon
        let text =
            ending
            ? "-" + RecordingClock.format(clock.remaining)
            : RecordingClock.format(clock.elapsed)
        let color =
            ending
            ? NSColor(calibratedRed: 1.0, green: 0.35, blue: 0.32, alpha: 0.95)
            : NSColor(calibratedWhite: 1.0, alpha: 0.52)
        let attributes: [NSAttributedString.Key: Any] = [
            .font: timerFont, .foregroundColor: color,
        ]
        let size = (text as NSString).size(withAttributes: attributes)
        (text as NSString).draw(
            at: NSPoint(
                x: content.maxX - padding - size.width, y: content.midY - size.height / 2),
            withAttributes: attributes)
    }

    private func drawTranscribing() {
        let time = CACurrentMediaTime()
        let text = "Transcribing"
        let attributes: [NSAttributedString.Key: Any] = [
            .font: labelFont,
            .foregroundColor: NSColor(calibratedWhite: 1.0, alpha: 0.92),
        ]
        let size = (text as NSString).size(withAttributes: attributes)
        let dotSize: CGFloat = 4
        let dotSpan = dotSize * 3 + 7
        let total = size.width + 8 + dotSpan
        var x = (content.width - total) / 2
        (text as NSString).draw(
            at: NSPoint(x: x, y: content.midY - size.height / 2),
            withAttributes: attributes
        )
        x += size.width + 8
        for index in 0..<3 {
            let phase = time * 3.6 - Double(index) * 0.5
            let alpha = 0.25 + 0.65 * (0.5 + 0.5 * sin(phase))
            let rect = NSRect(
                x: x, y: content.midY - dotSize / 2, width: dotSize, height: dotSize)
            NSColor(calibratedWhite: 1.0, alpha: alpha).setFill()
            NSBezierPath(ovalIn: rect).fill()
            x += dotSize + 3.5
        }
    }

    private func drawError(_ message: String) {
        let attributes: [NSAttributedString.Key: Any] = [
            .font: labelFont,
            .foregroundColor: NSColor(calibratedWhite: 1.0, alpha: 0.95),
        ]
        let size = (message as NSString).size(withAttributes: attributes)
        let total = dotSize + 8 + size.width
        var x = (content.width - total) / 2
        let dotRect = NSRect(x: x, y: content.midY - dotSize / 2, width: dotSize, height: dotSize)
        NSColor(calibratedRed: 1.0, green: 0.72, blue: 0.24, alpha: 0.95).setFill()
        NSBezierPath(ovalIn: dotRect).fill()
        x += dotSize + 8
        (message as NSString).draw(
            at: NSPoint(x: x, y: content.midY - size.height / 2),
            withAttributes: attributes
        )
    }

    // The long edge of the capsule, whichever way it is turned.
    func length(for state: IndicatorState) -> CGFloat {
        switch state {
        case .idle, .transcribing:
            return 118
        case .recording:
            return padding + dotSize + dotGap + barSpan + timerSpan + padding
        case .error(let message):
            let size = (message as NSString).size(withAttributes: [.font: labelFont])
            return min(max(size.width + 44, 120), 360)
        }
    }

    // The newest level goes on the right; the window is exactly as wide as the
    // bars, so nothing has to be trimmed anywhere else.
    func push(level: Double) {
        levels.append(level)
        if levels.count > barCount { levels.removeFirst(levels.count - barCount) }
    }

    func resetLevels() {
        levels = Array(repeating: 0, count: barCount)
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var panel: NSPanel!
    private var pill: PillView!
    private let meter = LevelMeter(url: wavFile)
    private var state: IndicatorState = .idle
    private var renderTimer: Timer?
    private var enteredVisibleState = CACurrentMediaTime()
    private var lastAudioGrowth = CACurrentMediaTime()
    private var pendingHide = false
    private var lastRaw = ""
    private let thickness: CGFloat = 26

    // A stuck state file would otherwise leave the pill on screen forever.
    // A real recording carries a deadline and is judged by that instead; this
    // only covers a token written by hand. Transcribing has to outlast the
    // longest recording the script allows.
    private let clocklessRecordingTimeout: Double = 185
    private let transcribingTimeout: Double = 900
    private let errorTimeout: Double = 2.6
    // ffmpeg writes samples even in a silent room, so a WAV that stops growing
    // means the recorder is gone. It also exits on its own at -t, and nothing
    // writes a state for that.
    private let stallTimeout: Double = 1.5

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        // Survives being launched hidden, by an older launcher or a login
        // item. A hidden app draws nothing, whatever the window level says.
        NSApp.unhide(nil)
        buildPanel()
        adoptInitialState()
        let poll = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            self?.pollState()
        }
        RunLoop.main.add(poll, forMode: .common)
    }

    // Launching takes a moment, so whisperkit-dictate may have written
    // "recording" before this process existed. Adopt a state that was written
    // in the last few seconds; treat anything older as leftover.
    private func adoptInitialState() {
        let raw = (try? String(contentsOf: stateFile, encoding: .utf8)) ?? ""
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        let modified =
            (try? FileManager.default.attributesOfItem(atPath: stateFile.path)[.modificationDate])
            as? Date
        let age = modified.map { Date().timeIntervalSince($0) } ?? .greatestFiniteMagnitude
        if trimmed != "quit", age < 5, let parsed = IndicatorState.parse(trimmed), parsed.isVisible {
            apply(parsed)
            return
        }
        write(state: "idle")
    }

    private func buildPanel() {
        let frame = NSRect(x: 0, y: 0, width: 121, height: thickness)
        panel = NSPanel(
            contentRect: frame,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.isFloatingPanel = true
        panel.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.overlayWindow)))
        panel.collectionBehavior = [
            .canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle,
        ]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        // Only clickable while it is on screen, which is only while dictating.
        // Idle it is ordered out, so it can never swallow a click.
        panel.ignoresMouseEvents = false
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.alphaValue = 0

        pill = PillView(frame: frame)
        pill.orientation = Anchor.saved
        pill.onDragEnded = { [weak self] in self?.snapAfterDrag() }
        panel.contentView = pill
    }

    // MARK: State polling

    private func pollState() {
        if let raw = try? String(contentsOf: stateFile, encoding: .utf8) {
            let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed == "quit" {
                try? FileManager.default.removeItem(at: pidFile)
                NSApp.terminate(nil)
                return
            }
            // Compare the raw token, not the parsed state: two recordings in a
            // row both parse to .recording but carry different deadlines.
            if trimmed != lastRaw, let parsed = IndicatorState.parse(trimmed) {
                lastRaw = trimmed
                pill.clock = parsed == .recording ? RecordingClock.parse(trimmed) : nil
                apply(parsed)
            }
        }
        expireStuckState()
    }

    // Only true with positive evidence that the recorder is gone: no new audio
    // and no live pid. Either one alone would hide the pill while it is wanted.
    private func recorderStalled() -> Bool {
        guard CACurrentMediaTime() - lastAudioGrowth > stallTimeout else { return false }
        let pidFile = stateDir.appendingPathComponent("ffmpeg.pid")
        guard let text = try? String(contentsOf: pidFile, encoding: .utf8),
            let pid = Int32(text.trimmingCharacters(in: .whitespacesAndNewlines))
        else { return true }
        return kill(pid, 0) != 0
    }

    private func expireStuckState() {
        guard state.isVisible, isStuck() else { return }
        write(state: "idle")
        apply(.idle)
    }

    private func isStuck() -> Bool {
        let elapsed = CACurrentMediaTime() - enteredVisibleState
        switch state {
        case .idle:
            return false
        case .recording:
            if recorderStalled() { return true }
            // The deadline is authoritative when the recording sent one. The
            // grace period lets the stop travel down the control pipe first,
            // so the pill moves on to transcribing rather than disappearing.
            guard let clock = pill.clock else { return elapsed > clocklessRecordingTimeout }
            return clock.remaining <= 0 && elapsed > 20
        case .transcribing:
            return elapsed > transcribingTimeout
        case .error:
            return elapsed > errorTimeout
        }
    }

    private func write(state: String) {
        // atomically: true writes a temp file and renames it, and unlike
        // replaceItemAt it does not require the destination to exist.
        try? state.write(to: stateFile, atomically: true, encoding: .utf8)
    }

    private func apply(_ next: IndicatorState) {
        let wasVisible = state.isVisible
        state = next
        pill.state = next
        enteredVisibleState = CACurrentMediaTime()

        if next == .recording {
            meter.reset()
            lastAudioGrowth = CACurrentMediaTime()
            pill.isWarming = true
            pill.resetLevels()
        } else {
            pill.clock = nil
        }

        if next.isVisible {
            resize(to: pill.length(for: next), animated: wasVisible)
            show()
            startRendering()
        } else {
            stopRendering()
            hide()
        }
    }

    // MARK: Presentation

    private func targetScreen() -> NSScreen {
        let mouse = NSEvent.mouseLocation
        return NSScreen.screens.first { NSMouseInRect(mouse, $0.frame, false) }
            ?? NSScreen.main
            ?? NSScreen.screens[0]
    }

    // Snap to the nearest anchor and remember it. The anchor is stored, not
    // the point, so the pill lands in the same corner on whichever screen the
    // cursor is on.
    private func snapAfterDrag() {
        let area = screenUnder(panel.frame).visibleFrame
        let anchor = Anchor.nearest(to: panel.frame, in: area)
        Anchor.saved = anchor
        pill.orientation = anchor
        // Turning onto a side edge changes the shape, not only the position.
        let size = anchor.size(length: pill.length(for: state), thickness: thickness)
        let frame = NSRect(origin: anchor.origin(size: size, in: area), size: size)
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.18
            context.timingFunction = CAMediaTimingFunction(name: .easeOut)
            panel.animator().setFrame(frame, display: true)
        }
        panel.invalidateShadow()
        if pendingHide {
            pendingHide = false
            hide()
        }
    }

    private func screenUnder(_ frame: NSRect) -> NSScreen {
        let center = NSPoint(x: frame.midX, y: frame.midY)
        return NSScreen.screens.first { NSMouseInRect(center, $0.frame, false) }
            ?? targetScreen()
    }

    private func resize(to length: CGFloat, animated: Bool) {
        if pill.isDragging { return }
        let anchor = Anchor.saved
        pill.orientation = anchor
        let size = anchor.size(length: length.rounded(), thickness: thickness)
        let screen = targetScreen().visibleFrame
        let frame = NSRect(origin: anchor.origin(size: size, in: screen), size: size)
        if animated && panel.alphaValue > 0 {
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.16
                context.timingFunction = CAMediaTimingFunction(name: .easeOut)
                panel.animator().setFrame(frame, display: true)
            }
        } else {
            panel.setFrame(frame, display: false)
        }
        panel.invalidateShadow()
    }

    private func show() {
        // Order in every time, with no guard on the current alpha. A guard here
        // latches: one hide that leaves alpha at 1 while the window is ordered
        // out, and the pill never comes back.
        // orderFrontRegardless, never makeKey: dictation pastes into whatever
        // the user was already typing in, so this must not take focus.
        panel.orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.14
            panel.animator().alphaValue = 1
        }
    }

    private func hide() {
        // Never yank the pill out from under a drag in progress.
        if pill.isDragging {
            pendingHide = true
            return
        }
        NSAnimationContext.runAnimationGroup({ context in
            context.duration = 0.18
            panel.animator().alphaValue = 0
        }, completionHandler: { [weak self] in
            guard let self else { return }
            if !self.state.isVisible { self.panel.orderOut(nil) }
        })
    }

    // MARK: Rendering

    private func startRendering() {
        guard renderTimer == nil else { return }
        let timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) {
            [weak self] _ in
            self?.tick()
        }
        RunLoop.main.add(timer, forMode: .common)
        renderTimer = timer
    }

    private func stopRendering() {
        renderTimer?.invalidate()
        renderTimer = nil
    }

    private func tick() {
        if state == .recording {
            let level = meter.sample()
            if level != nil {
                lastAudioGrowth = CACurrentMediaTime()
                pill.isWarming = false
            }
            pill.push(level: level ?? 0)
        }
        pill.needsDisplay = true
    }

    func applicationWillTerminate(_ notification: Notification) {
        try? FileManager.default.removeItem(at: pidFile)
    }
}

// MARK: - Single instance

func alreadyRunning() -> Bool {
    guard let text = try? String(contentsOf: pidFile, encoding: .utf8),
        let pid = Int32(text.trimmingCharacters(in: .whitespacesAndNewlines)),
        pid != ProcessInfo.processInfo.processIdentifier
    else { return false }
    // A pid file outlives its process, and macOS reuses pids. Signal 0 only
    // says something is alive, so also check that it is this program.
    guard kill(pid, 0) == 0 else { return false }
    let running = NSRunningApplication(processIdentifier: pid)
    return running?.bundleIdentifier == Bundle.main.bundleIdentifier
}

try? FileManager.default.createDirectory(at: stateDir, withIntermediateDirectories: true)
if alreadyRunning() { exit(0) }
try? String(ProcessInfo.processInfo.processIdentifier).write(
    to: pidFile, atomically: true, encoding: .utf8)

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
