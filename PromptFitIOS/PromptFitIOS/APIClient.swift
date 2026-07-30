import Foundation

struct APIClient {
    var baseURL: URL

    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        return decoder
    }()

    func healthCheck() async throws {
        var request = URLRequest(url: endpoint("/api/garmin/local-fits"))
        request.timeoutInterval = 8
        let (_, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: Data())
    }

    func generateFIT(
        prompt: String,
        raceDistance: RaceDistance,
        referencePace: String,
        paces: [String: String],
        provider: String,
        model: String,
        apiKey: String,
        targets: Bool
    ) async throws -> (data: Data, filename: String) {
        let payload: [String: Any] = [
            "prompt": prompt,
            "provider": provider,
            "openai_api_key": provider == "openai" || provider == "auto" ? apiKey : "",
            "openai_model": provider == "openai" || provider == "auto" ? model : "",
            "openrouter_api_key": provider == "openrouter" ? apiKey : "",
            "openrouter_model": provider == "openrouter" ? model : "",
            "race_distance": raceDistance.rawValue,
            "hmp": referencePace,
            "paces": paces,
            "targets": targets,
            "target_mode": "pace",
            "target_margin": "30",
            "sideload": false,
        ]
        let body = try JSONSerialization.data(withJSONObject: payload)
        var request = URLRequest(url: endpoint("/api/prompt-to-fit"))
        request.httpMethod = "POST"
        request.httpBody = body
        request.timeoutInterval = 90
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: data)
        guard let http = response as? HTTPURLResponse else {
            throw APIError(message: "The Mac returned an unreadable response.")
        }
        let filename = Self.filename(from: http) ?? "Workout.fit"
        guard filename.lowercased().hasSuffix(".fit") else {
            throw APIError(message: "This description created more than one workout. Please generate one workout at a time in the iPhone app.")
        }
        return (data, filename)
    }

    func previewJSON(
        prompt: String,
        raceDistance: RaceDistance,
        referencePace: String,
        paces: [String: String],
        provider: String,
        model: String,
        apiKey: String
    ) async throws -> String {
        let payload: [String: Any] = [
            "prompt": prompt,
            "provider": provider,
            "openai_api_key": provider == "openai" || provider == "auto" ? apiKey : "",
            "openai_model": provider == "openai" || provider == "auto" ? model : "",
            "openrouter_api_key": provider == "openrouter" ? apiKey : "",
            "openrouter_model": provider == "openrouter" ? model : "",
            "race_distance": raceDistance.rawValue,
            "hmp": referencePace,
            "paces": paces,
        ]
        let body = try JSONSerialization.data(withJSONObject: payload)
        var request = URLRequest(url: endpoint("/api/preview-plan"))
        request.httpMethod = "POST"
        request.httpBody = body
        request.timeoutInterval = 90
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: data)
        let object = try JSONSerialization.jsonObject(with: data)
        let pretty = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
        return String(data: pretty, encoding: .utf8) ?? "{}"
    }

    func parseFIT(_ data: Data, filename: String) async throws -> ParsedFITResult {
        let boundary = "PromptFit-\(UUID().uuidString)"
        let body = multipartBody(
            boundary: boundary,
            fieldName: "files",
            filename: filename,
            data: data
        )
        var request = URLRequest(url: endpoint("/api/parse-fit"))
        request.httpMethod = "POST"
        request.httpBody = body
        request.timeoutInterval = 30
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        let (responseData, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: responseData)
        let envelope = try decoder.decode(ParsedFITEnvelope.self, from: responseData)
        guard let result = envelope.results.first else {
            throw APIError(message: "No workout graph was returned.")
        }
        if let error = result.graph.error {
            throw APIError(message: error)
        }
        return result
    }

    func approveFIT(_ data: Data, filename: String) async throws -> LocalFITFile {
        let boundary = "PromptFit-\(UUID().uuidString)"
        let body = multipartBody(
            boundary: boundary,
            fieldName: "file",
            filename: filename,
            data: data
        )
        var request = URLRequest(url: endpoint("/api/fit-review/approve"))
        request.httpMethod = "POST"
        request.httpBody = body
        request.timeoutInterval = 30
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        let (responseData, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: responseData)
        return try decoder.decode(ApprovalEnvelope.self, from: responseData).file
    }

    func fetchQueue() async throws -> [LocalFITFile] {
        var request = URLRequest(url: endpoint("/api/garmin/local-fits"))
        request.timeoutInterval = 15
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: data)
        return try decoder.decode(LocalFITEnvelope.self, from: data).files
    }

    func downloadFIT(id: String) async throws -> (data: Data, filename: String) {
        var components = URLComponents(url: endpoint("/api/garmin/local-fit-download"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "file", value: id)]
        var request = URLRequest(url: components.url!)
        request.timeoutInterval = 20
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: data)
        let filename = (response as? HTTPURLResponse).flatMap(Self.filename(from:)) ?? URL(fileURLWithPath: id).lastPathComponent
        return (data, filename)
    }

    func garminStatus() async throws -> GarminStatus {
        var request = URLRequest(url: endpoint("/api/garmin/status"))
        request.timeoutInterval = 30
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: data)
        return try decoder.decode(GarminStatus.self, from: data)
    }

    func uploadToGarmin(fileIDs: [String], scheduleDate: String) async throws -> GarminUploadReport {
        let body = try JSONSerialization.data(withJSONObject: ["files": fileIDs, "schedule_date": scheduleDate])
        var request = URLRequest(url: endpoint("/api/garmin/local-fit-upload"))
        request.httpMethod = "POST"
        request.httpBody = body
        request.timeoutInterval = 90
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: data)
        return try decoder.decode(GarminUploadReport.self, from: data)
    }

    private func endpoint(_ path: String) -> URL {
        let trimmed = path.hasPrefix("/") ? String(path.dropFirst()) : path
        return baseURL.appendingPathComponent(trimmed)
    }

    private func multipartBody(boundary: String, fieldName: String, filename: String, data: Data) -> Data {
        var body = Data()
        body.append("--\(boundary)\r\n")
        body.append("Content-Disposition: form-data; name=\"\(fieldName)\"; filename=\"\(filename)\"\r\n")
        body.append("Content-Type: application/octet-stream\r\n\r\n")
        body.append(data)
        body.append("\r\n--\(boundary)--\r\n")
        return body
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else {
            throw APIError(message: "No response came back from the Mac.")
        }
        guard (200..<300).contains(http.statusCode) else {
            let fallback = String(data: data, encoding: .utf8) ?? "Request failed (\(http.statusCode))."
            let detail: String
            if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let serverDetail = object["detail"] as? String {
                detail = serverDetail
            } else {
                detail = fallback
            }
            throw APIError(message: detail)
        }
    }

    private static func filename(from response: HTTPURLResponse) -> String? {
        guard let disposition = response.value(forHTTPHeaderField: "Content-Disposition") else { return nil }
        let marker = "filename="
        guard let range = disposition.range(of: marker, options: .caseInsensitive) else { return nil }
        return String(disposition[range.upperBound...]).trimmingCharacters(in: CharacterSet(charactersIn: "\"' "))
    }
}

private extension Data {
    mutating func append(_ string: String) {
        if let data = string.data(using: .utf8) { append(data) }
    }
}
