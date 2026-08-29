import Foundation

// A launchable wrapper around `whisperkit-dictate toggle`.
//
// Mouse software such as Logi Options+ can open an application, but cannot run
// a shell command. This is that application: every launch toggles dictation,
// so one mouse button starts a recording and the next click ends it.
//
// It runs to completion and exits, holding no state of its own. The listener
// owns the recording; this only sends it a word.

let command = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".local/bin/whisperkit-dictate")

guard FileManager.default.isExecutableFile(atPath: command.path) else {
    FileHandle.standardError.write(
        Data("Missing executable: \(command.path)\n".utf8)
    )
    exit(1)
}

let toggle = Process()
toggle.executableURL = command
toggle.arguments = ["toggle"]

do {
    try toggle.run()
    toggle.waitUntilExit()
    exit(toggle.terminationStatus)
} catch {
    FileHandle.standardError.write(Data("Could not toggle dictation: \(error)\n".utf8))
    exit(1)
}
