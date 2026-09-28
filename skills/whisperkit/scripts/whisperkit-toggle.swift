import Foundation

// Logi Options+ can open an app but cannot run a shell command. Each launch
// sends one word to the Dictation listener, which is what holds the microphone.
// This process never opens the mic.

let stateDir = FileManager.default.temporaryDirectory
    .appendingPathComponent("whisperkit-dictate", isDirectory: true)
let control = stateDir.appendingPathComponent("control")
let dictation = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Applications/WhisperKit Dictation.app")

func sendToggle() -> Bool {
    let fd = open(control.path, O_WRONLY | O_NONBLOCK)
    guard fd >= 0 else { return false }
    defer { close(fd) }
    var line = "toggle\n"
    return line.withUTF8 { bytes in
        guard let base = bytes.baseAddress else { return false }
        return write(fd, base, bytes.count) > 0
    }
}

func openDictation() {
    guard FileManager.default.fileExists(atPath: dictation.path) else { return }
    let task = Process()
    task.executableURL = URL(fileURLWithPath: "/usr/bin/open")
    task.arguments = ["-g", dictation.path]
    try? task.run()
    task.waitUntilExit()
}

if sendToggle() {
    exit(0)
}

openDictation()
for _ in 0..<20 {
    Thread.sleep(forTimeInterval: 0.15)
    if sendToggle() {
        exit(0)
    }
}

FileHandle.standardError.write(
    Data("Dictation listener is not running. Open WhisperKit Dictation.\n".utf8)
)
exit(1)
