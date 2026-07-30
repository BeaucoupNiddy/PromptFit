import Foundation
import SwiftUI
import Combine

@MainActor
final class WorkoutStore: ObservableObject {
    static let defaultCompanionURL = "http://MacBook-Pro.local:8000"

    @Published var prompt = ""
    @Published var selectedPresetID = ""
    @Published var raceDistance: RaceDistance {
        didSet { UserDefaults.standard.set(raceDistance.rawValue, forKey: "raceDistance") }
    }
    @Published var referencePace: String {
        didSet { UserDefaults.standard.set(referencePace, forKey: "referencePace") }
    }
    @Published var paceProfile: [String: String] {
        didSet { UserDefaults.standard.set(paceProfile, forKey: "paceProfile") }
    }
    @Published var targetsEnabled: Bool {
        didSet { UserDefaults.standard.set(targetsEnabled, forKey: "targetsEnabled") }
    }

    @Published var draft: WorkoutDraft?
    @Published var queue: [LocalFITFile] = []
    @Published var selectedQueueIDs: Set<String> = []
    @Published var currentFITID = ""
    @Published var garminStatus: GarminStatus = .unknown
    @Published var uploadReport: GarminUploadReport?
    @Published var JSONPreview = ""
    @Published var garminWorkoutDate = Date()

    @Published var isGenerating = false
    @Published var isApproving = false
    @Published var isUploading = false
    @Published var isCheckingConnection = false
    @Published var isLoadingQueue = false
    @Published var isPreviewingJSON = false
    @Published var statusMessage = "Enter your own workout or choose a preset to get started."
    @Published var errorMessage: String?
    @Published var shareFile: ShareableFIT?

    @Published var companionURLString: String {
        didSet { UserDefaults.standard.set(companionURLString, forKey: "companionURL") }
    }
    @Published var provider: String {
        didSet { UserDefaults.standard.set(provider, forKey: "aiProvider") }
    }
    @Published var model: String {
        didSet { UserDefaults.standard.set(model, forKey: "aiModel") }
    }

    init() {
        let defaults = UserDefaults.standard
        let savedCompanionURL = defaults.string(forKey: "companionURL")?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if defaults.bool(forKey: "didSeedMacBookCompanionURL_v1") {
            companionURLString = savedCompanionURL.isEmpty ? Self.defaultCompanionURL : savedCompanionURL
        } else {
            companionURLString = Self.defaultCompanionURL
            defaults.set(Self.defaultCompanionURL, forKey: "companionURL")
            defaults.set(true, forKey: "didSeedMacBookCompanionURL_v1")
        }
        provider = defaults.string(forKey: "aiProvider") ?? "openai"
        model = defaults.string(forKey: "aiModel") ?? "gpt-4o-mini"
        raceDistance = RaceDistance(rawValue: defaults.string(forKey: "raceDistance") ?? "") ?? .halfMarathon
        referencePace = defaults.string(forKey: "referencePace") ?? ""
        paceProfile = defaults.dictionary(forKey: "paceProfile") as? [String: String] ?? [:]
        targetsEnabled = defaults.object(forKey: "targetsEnabled") as? Bool ?? false
        currentFITID = defaults.string(forKey: "currentFITID") ?? ""
    }

    var apiKey: String {
        get { KeychainStore.read("ai-api-key") }
        set { KeychainStore.write(newValue, account: "ai-api-key") }
    }

    var companionURL: URL? {
        let trimmed = companionURLString.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let normalized = trimmed.contains("://") ? trimmed : "http://\(trimmed)"
        return URL(string: normalized.trimmingCharacters(in: CharacterSet(charactersIn: "/")))
    }

    var orderedQueue: [LocalFITFile] {
        queue.sorted { left, right in
            if left.id == currentFITID { return true }
            if right.id == currentFITID { return false }
            return (left.modified ?? "") > (right.modified ?? "")
        }
    }

    var canGenerate: Bool {
        !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && companionURL != nil && !isGenerating
    }

    func applyPreset(_ id: String) {
        selectedPresetID = id
        guard let preset = WorkoutPreset.all.first(where: { $0.id == id }) else { return }
        prompt = preset.prompt
        statusMessage = "Preset loaded. Change any wording before generating."
    }

    func generate() async {
        guard let client = client() else { return }
        let cleanPrompt = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanPrompt.isEmpty else {
            errorMessage = "Describe a workout first."
            return
        }
        isGenerating = true
        draft = nil
        currentFITID = ""
        statusMessage = "Creating your workout and drawing the graph…"
        defer { isGenerating = false }
        do {
            let generated = try await client.generateFIT(
                prompt: cleanPrompt,
                raceDistance: raceDistance,
                referencePace: referencePace,
                paces: paceProfile,
                provider: provider,
                model: model,
                apiKey: apiKey,
                targets: targetsEnabled
            )
            let parsed = try await client.parseFIT(generated.data, filename: generated.filename)
            draft = WorkoutDraft(
                data: generated.data,
                filename: generated.filename,
                graph: parsed.graph,
                summary: parsed.summary ?? "",
                originalPrompt: cleanPrompt,
                approvedFile: nil
            )
            statusMessage = "Review the graph, then approve the workout or modify its description."
        } catch {
            present(error)
        }
    }

    func previewInterpretedJSON() async {
        guard let client = client() else { return }
        let cleanPrompt = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanPrompt.isEmpty else {
            errorMessage = "Describe a workout first."
            return
        }
        isPreviewingJSON = true
        defer { isPreviewingJSON = false }
        do {
            JSONPreview = try await client.previewJSON(
                prompt: cleanPrompt,
                raceDistance: raceDistance,
                referencePace: referencePace,
                paces: paceProfile,
                provider: provider,
                model: model,
                apiKey: apiKey
            )
        } catch {
            present(error)
        }
    }

    func approve() async {
        guard let client = client(), var pending = draft else { return }
        isApproving = true
        statusMessage = "Approving and adding this workout to the top of your queue…"
        defer { isApproving = false }
        do {
            let file = try await client.approveFIT(pending.data, filename: pending.filename)
            pending.approvedFile = file
            pending.filename = file.name
            draft = pending
            currentFITID = file.id
            UserDefaults.standard.set(file.id, forKey: "currentFITID")
            selectedQueueIDs.insert(file.id)
            await loadQueue()
            statusMessage = "Approved and selected. Choose Save or Upload to Garmin."
            await refreshGarminStatus()
        } catch {
            present(error)
        }
    }

    func modifyDraft() {
        guard let current = draft else { return }
        prompt = current.originalPrompt
        draft = nil
        currentFITID = ""
        statusMessage = "Update the description, then generate a new version for review."
    }

    func loadQueue() async {
        guard let client = client(showError: false) else { return }
        isLoadingQueue = true
        defer { isLoadingQueue = false }
        do {
            queue = try await client.fetchQueue()
            if currentFITID.isEmpty {
                currentFITID = UserDefaults.standard.string(forKey: "currentFITID") ?? ""
            }
            if !currentFITID.isEmpty, queue.contains(where: { $0.id == currentFITID }) {
                selectedQueueIDs.insert(currentFITID)
            }
            selectedQueueIDs = selectedQueueIDs.intersection(Set(queue.map(\.id)))
        } catch {
            present(error)
        }
    }

    func refreshGarminStatus() async {
        guard let client = client(showError: false) else { return }
        isCheckingConnection = true
        defer { isCheckingConnection = false }
        do {
            garminStatus = try await client.garminStatus()
        } catch {
            garminStatus = .unknown
        }
    }

    func uploadCurrent() async {
        guard let fileID = draft?.approvedFile?.id else {
            errorMessage = "Approve this workout before uploading it."
            return
        }
        await upload(fileIDs: [fileID])
    }

    func uploadSelected() async {
        let IDs = orderedQueue.map(\.id).filter { selectedQueueIDs.contains($0) }
        guard !IDs.isEmpty else {
            errorMessage = "Select at least one workout first."
            return
        }
        await upload(fileIDs: IDs)
    }

    private func upload(fileIDs: [String]) async {
        guard let client = client() else { return }
        isUploading = true
        defer { isUploading = false }
        do {
            garminStatus = try await client.garminStatus()
            guard garminStatus.connected else {
                throw APIError(message: "Garmin is not connected. Open PromptFit on the Mac once and connect Garmin there.")
            }
            let date = Self.garminDateFormatter.string(from: garminWorkoutDate)
            uploadReport = try await client.uploadToGarmin(fileIDs: fileIDs, scheduleDate: date)
            statusMessage = uploadReport?.message ?? "Garmin upload finished for \(date)."
        } catch {
            present(error)
        }
    }

    func prepareShare(for file: LocalFITFile? = nil) async {
        guard let client = client() else { return }
        do {
            if let file {
                let download = try await client.downloadFIT(id: file.id)
                shareFile = try ShareableFIT(data: download.data, filename: download.filename)
            } else if let draft {
                shareFile = try ShareableFIT(data: draft.data, filename: draft.filename)
            }
        } catch {
            present(error)
        }
    }

    func testCompanion() async -> Bool {
        guard let client = client() else { return false }
        do {
            try await client.healthCheck()
            statusMessage = "Connected to PromptFit on your Mac."
            return true
        } catch {
            present(APIError(message: "The iPhone could not reach PromptFit. Confirm both devices are on the same Wi‑Fi and the Mac window is still running."))
            return false
        }
    }

    private func client(showError: Bool = true) -> APIClient? {
        guard let url = companionURL else {
            if showError { errorMessage = "Add the Mac companion address in Settings first." }
            return nil
        }
        return APIClient(baseURL: url)
    }

    private func present(_ error: Error) {
        errorMessage = error.localizedDescription
        statusMessage = "Nothing was downloaded or uploaded."
    }

    private static let garminDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()
}

struct ShareableFIT: Identifiable {
    let id = UUID()
    let url: URL

    init(data: Data, filename: String) throws {
        let safeName = filename.replacingOccurrences(of: "/", with: "-")
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("PromptFitShare", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let target = folder.appendingPathComponent(safeName)
        try data.write(to: target, options: .atomic)
        url = target
    }
}
