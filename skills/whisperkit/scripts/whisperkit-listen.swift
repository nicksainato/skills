import Foundation

let script = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".local/bin/whisperkit-dictate")

guard FileManager.default.isExecutableFile(atPath: script.path) else {
    FileHandle.standardError.write(
        Data("Missing executable: \(script.path)\n".utf8)
    )
    exit(1)
}

let listener = Process()
listener.executableURL = script
listener.arguments = ["listen"]

signal(SIGINT, SIG_IGN)
signal(SIGTERM, SIG_IGN)

let interruptSource = DispatchSource.makeSignalSource(signal: SIGINT)
let terminateSource = DispatchSource.makeSignalSource(signal: SIGTERM)
for source in [interruptSource, terminateSource] {
    source.setEventHandler {
        if listener.isRunning {
            listener.terminate()
        }
    }
    source.resume()
}

do {
    try listener.run()
    listener.waitUntilExit()
    exit(listener.terminationStatus)
} catch {
    FileHandle.standardError.write(Data("Could not start dictation: \(error)\n".utf8))
    exit(1)
}
