import Foundation

struct WorkoutPreset: Identifiable, Hashable {
    let id: String
    let title: String
    let prompt: String

    static let all: [WorkoutPreset] = [
        .init(
            id: "easy-strides",
            title: "45-minute easy run with relaxed strides",
            prompt: "Run 45 minutes at an easy conversational effort. After 30 minutes, include 6 × 20-second relaxed strides with 60 seconds of easy jogging between each. Finish easy."
        ),
        .init(
            id: "progression",
            title: "60-minute progression run",
            prompt: "Run 60 minutes as a progression: 25 minutes easy, 20 minutes steady, 10 minutes at threshold effort, then 5 minutes easy to cool down."
        ),
        .init(
            id: "threshold-cruise",
            title: "Threshold cruise intervals",
            prompt: "Warm up for 15 minutes easy, then run 3 × 10 minutes at threshold effort with 2 minutes of easy jogging between repetitions. Cool down for 10 minutes easy."
        ),
        .init(
            id: "six-by-800",
            title: "6 × 800 meters at 5K effort",
            prompt: "Warm up for 15 minutes easy, then run 6 × 800 meters at 5K effort with 400 meters of easy jogging after each repetition. Cool down for 10 minutes easy."
        ),
        .init(
            id: "ten-k-repeats",
            title: "5 × 5 minutes at 10K effort",
            prompt: "Warm up for 15 minutes easy, then run 5 × 5 minutes at 10K effort with 2 minutes of easy jogging between repetitions. Cool down for 10 minutes easy."
        ),
        .init(
            id: "hill-repeats",
            title: "Short hill repeats with jog-down recovery",
            prompt: "Warm up for 15 minutes easy, then run 10 × 60 seconds hard uphill with an easy jog back down after each repeat. Finish with 10 minutes easy."
        ),
        .init(
            id: "fartlek",
            title: "10 × 1-minute fartlek",
            prompt: "Warm up for 12 minutes easy, then run 10 × 1 minute fast with 1 minute easy between each effort. Cool down for 10 minutes easy."
        ),
        .init(
            id: "long-fast-finish",
            title: "Long run with a fast finish",
            prompt: "Run 90 minutes easy, then 20 minutes at marathon effort, followed by 10 minutes easy to cool down."
        ),
        .init(
            id: "race-sharpening",
            title: "Race-week sharpening session",
            prompt: "Warm up for 15 minutes easy, then run 4 × 2 minutes at 5K effort with 2 minutes easy jogging between repetitions. Add 4 × 20-second relaxed strides with 60 seconds easy, then cool down for 10 minutes."
        ),
    ]
}

enum RaceDistance: String, CaseIterable, Identifiable {
    case fiveK = "5k"
    case tenK = "10k"
    case halfMarathon = "half marathon"
    case marathon

    var id: String { rawValue }

    var label: String {
        switch self {
        case .fiveK: "5K"
        case .tenK: "10K"
        case .halfMarathon: "Half marathon"
        case .marathon: "Marathon"
        }
    }
}

enum PaceAnchorKind: String, CaseIterable, Identifiable {
    case easy
    case marathon
    case halfMarathon = "half_marathon"
    case threshold
    case tenK = "10k"
    case fiveK = "5k"
    case threeK = "3k"
    case mile

    var id: String { rawValue }

    var label: String {
        switch self {
        case .easy: "Easy / conversational"
        case .marathon: "Marathon pace"
        case .halfMarathon: "Half marathon pace"
        case .threshold: "Lactate threshold / T"
        case .tenK: "10K pace"
        case .fiveK: "5K pace"
        case .threeK: "3K pace"
        case .mile: "Mile / repetition pace"
        }
    }

    var placeholder: String {
        switch self {
        case .easy: "8:15 /mi"
        case .marathon: "7:05 /mi"
        case .halfMarathon: "6:45 /mi"
        case .threshold: "6:38 /mi"
        case .tenK: "6:30 /mi"
        case .fiveK: "6:12 /mi"
        case .threeK: "6:00 /mi"
        case .mile: "5:40 /mi"
        }
    }
}

struct GraphSegment: Codable, Identifiable, Hashable {
    var id: UUID = UUID()
    let durationSeconds: Double
    let paceMinutesPerMile: Double?
    let intensity: String?
    let label: String?
    let durationInferred: Bool?

    enum CodingKeys: String, CodingKey {
        case durationSeconds = "duration_s"
        case paceMinutesPerMile = "pace_min_per_mi"
        case intensity
        case label
        case durationInferred = "duration_inferred"
    }
}

struct WorkoutGraph: Codable, Hashable {
    let segments: [GraphSegment]
    let totalSeconds: Double
    let inferredSeconds: Double?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case segments
        case totalSeconds = "total_seconds"
        case inferredSeconds = "inferred_seconds"
        case error
    }

    static let empty = WorkoutGraph(segments: [], totalSeconds: 0, inferredSeconds: nil, error: nil)
}

struct ParsedFITResult: Codable {
    let name: String?
    let summary: String?
    let graph: WorkoutGraph
}

struct ParsedFITEnvelope: Codable {
    let results: [ParsedFITResult]
}

struct LocalFITFile: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let folder: String?
    let size: Int
    let modified: String?
}

struct LocalFITEnvelope: Codable {
    let files: [LocalFITFile]
    let count: Int
    let folder: String?
}

struct ApprovalEnvelope: Codable {
    let ok: Bool
    let file: LocalFITFile
}

struct GarminStatus: Codable, Equatable {
    let status: String
    let connected: Bool
    let verified: Bool
    let savedSession: Bool?
    let canManage: Bool?
    let checkedAt: String?
    let accountName: String?
    let message: String
    let verificationError: String?

    enum CodingKeys: String, CodingKey {
        case status, connected, verified, message
        case savedSession = "saved_session"
        case canManage = "can_manage"
        case checkedAt = "checked_at"
        case accountName = "account_name"
        case verificationError = "verification_error"
    }

    static let unknown = GarminStatus(
        status: "unknown",
        connected: false,
        verified: false,
        savedSession: nil,
        canManage: nil,
        checkedAt: nil,
        accountName: nil,
        message: "Connection has not been checked yet.",
        verificationError: nil
    )
}

struct GarminUploadResult: Codable, Identifiable {
    var id: String { sourceID ?? source }
    let source: String
    let sourceID: String?
    let ok: Bool
    let workoutID: FlexibleIdentifier?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case source, ok, error
        case sourceID = "sourceId"
        case workoutID = "workoutId"
    }
}

enum FlexibleIdentifier: Codable, CustomStringConvertible {
    case string(String)
    case integer(Int)

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Int.self) {
            self = .integer(value)
        } else {
            self = .string(try container.decode(String.self))
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .integer(let value): try container.encode(value)
        }
    }

    var description: String {
        switch self {
        case .string(let value): value
        case .integer(let value): String(value)
        }
    }
}

struct GarminUploadReport: Codable, Identifiable {
    let id = UUID()
    let status: String
    let attempted: Int
    let successful: Int
    let failed: Int
    let completedAt: String?
    let results: [GarminUploadResult]
    let message: String

    enum CodingKeys: String, CodingKey {
        case status, attempted, successful, failed, results, message
        case completedAt = "completed_at"
    }
}

struct WorkoutDraft: Identifiable {
    let id = UUID()
    var data: Data
    var filename: String
    var graph: WorkoutGraph
    var summary: String
    var originalPrompt: String
    var approvedFile: LocalFITFile?

    var isApproved: Bool { approvedFile != nil }
}

struct APIError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}
