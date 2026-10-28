export interface TextDocument { id: string; title: string; text: string }
export interface TextChunk { id: string; document: string; metadata: { source: string; sourceId: string; chunk: number; total: number } }

export function splitText(text: string, size = 350, overlap = 50): string[] {
  if (!Number.isInteger(size) || !Number.isInteger(overlap) || size <= overlap || overlap < 0) throw new Error('分块参数无效');
  const chars = Array.from(text.replace(/\r\n?/g, '\n').normalize('NFC').trim());
  const result: string[] = [];
  for (let start = 0; start < chars.length;) {
    let end = Math.min(start + size, chars.length);
    if (end < chars.length) {
      for (let i = end - 1; i > start + size * 0.65; i--) {
        if (/[。！？\n.!?]/.test(chars[i])) { end = i + 1; break; }
      }
    }
    const part = chars.slice(start, end).join('').trim();
    if (part) result.push(part);
    if (end === chars.length) break;
    start = end - overlap;
  }
  return result;
}
export async function prepareDocuments(documents: TextDocument[]): Promise<TextChunk[]> {
  const chunks: TextChunk[] = [];
  for (const doc of documents) {
    const parts = splitText(doc.text);
    // Content-addressed IDs make interrupted/repeated imports idempotent.
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`${doc.id}\0${doc.title}\0${doc.text}`));
    const hash = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
    parts.forEach((document, chunk) => chunks.push({ id: `${hash}_${chunk}`, document, metadata: { source: doc.title, sourceId: doc.id, chunk, total: parts.length } }));
  }
  return chunks;
}
