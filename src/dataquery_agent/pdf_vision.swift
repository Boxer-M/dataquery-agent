import CoreGraphics
import Foundation
import Vision

struct Block: Codable {
    let text: String
    let confidence: Float
    let bounds: [CGFloat]
}

struct OCRPage: Codable {
    let page: Int
    let blocks: [Block]
    let error: String?
}

struct OCRResult: Codable {
    let extractor_version: String
    let pages: [OCRPage]
}

enum PageFailure: String, Error {
    case invalid_page
    case invalid_page_bounds
    case render_failed
    case language_unavailable
    case ocr_output_limit
}

func recognize(_ page: CGPDFPage) throws -> [Block] {
    let box = page.getBoxRect(.cropBox)
    let rotated = abs(page.rotationAngle % 180) == 90
    let width = rotated ? box.height : box.width
    let height = rotated ? box.width : box.height
    guard width.isFinite, height.isFinite, width > 0, height > 0 else {
        throw PageFailure.invalid_page_bounds
    }
    // Target 200 dpi with hard bounds on both raster dimensions and pixel count.
    let scale = min(200.0 / 72, 3200 / max(width, height), sqrt(6_000_000 / width / height))
    let w = max(1, Int((width * scale).rounded(.down)))
    let h = max(1, Int((height * scale).rounded(.down)))
    guard let context = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8,
                                  bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else {
        throw PageFailure.render_failed
    }
    let target = CGRect(x: 0, y: 0, width: w, height: h)
    context.setFillColor(gray: 1, alpha: 1)
    context.fill(target)
    context.scaleBy(x: scale, y: scale)
    let pageTarget = CGRect(x: 0, y: 0, width: CGFloat(w) / scale, height: CGFloat(h) / scale)
    context.concatenate(page.getDrawingTransform(.cropBox, rect: pageTarget, rotate: 0, preserveAspectRatio: true))
    context.drawPDFPage(page)
    guard let image = context.makeImage() else { throw PageFailure.render_failed }

    let request = VNRecognizeTextRequest()
    request.revision = VNRecognizeTextRequestRevision3
    request.recognitionLevel = .accurate
    let languages = ["zh-Hans", "en-US"]
    let supported = try request.supportedRecognitionLanguages()
    guard languages.allSatisfy({ supported.contains($0) }) else { throw PageFailure.language_unavailable }
    request.recognitionLanguages = languages
    try VNImageRequestHandler(cgImage: image).perform([request])

    let observations = request.results ?? []
    guard observations.count <= 500 else { throw PageFailure.ocr_output_limit }
    let blocks = observations.sorted {
        if $0.boundingBox.midY == $1.boundingBox.midY {
            return $0.boundingBox.minX < $1.boundingBox.minX
        }
        return $0.boundingBox.midY > $1.boundingBox.midY
    }.compactMap { observation -> Block? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox.intersection(CGRect(x: 0, y: 0, width: 1, height: 1))
        guard !box.isNull, !box.isEmpty else { return nil }
        return Block(text: candidate.string, confidence: candidate.confidence,
                     bounds: [box.minX, box.minY, box.width, box.height])
    }
    guard blocks.reduce(0, { $0 + $1.text.utf8.count }) <= 50_000 else { throw PageFailure.ocr_output_limit }
    return blocks
}

let args = CommandLine.arguments
guard args.count >= 3, let document = CGPDFDocument(URL(fileURLWithPath: args[1]) as CFURL) else {
    exit(1)
}
let pages = args.dropFirst(2).map { argument -> OCRPage in
    autoreleasepool {
        guard let number = Int(argument), let page = document.page(at: number) else {
            return OCRPage(page: Int(argument) ?? 0, blocks: [], error: PageFailure.invalid_page.rawValue)
        }
        do {
            return OCRPage(page: number, blocks: try recognize(page), error: nil)
        } catch {
            return OCRPage(page: number, blocks: [], error: (error as? PageFailure)?.rawValue ?? "ocr_failed")
        }
    }
}
let version = "Apple Vision revision 3, " + ProcessInfo.processInfo.operatingSystemVersionString
let output = try JSONEncoder().encode(OCRResult(extractor_version: version, pages: pages))
FileHandle.standardOutput.write(output)
