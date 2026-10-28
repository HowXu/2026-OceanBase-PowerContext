export function recallAtK(ids: string[], relevant: string[], k: number): number {
  const truth = new Set(relevant);
  if (!truth.size) return 0;
  return new Set(ids.slice(0, k).filter(id => truth.has(id))).size / truth.size;
}
export function ndcgAtK(ids: string[], relevant: string[], k: number): number {
  const truth = new Set(relevant);
  const seen = new Set<string>();
  const dcg = ids.slice(0, k).reduce((sum, id, i) => {
    const gain = truth.has(id) && !seen.has(id) ? 1 : 0;
    seen.add(id);
    return sum + gain / Math.log2(i + 2);
  }, 0);
  const ideal = Array.from({ length: Math.min(k, truth.size) }, (_, i) => 1 / Math.log2(i + 2)).reduce((a, b) => a + b, 0);
  return ideal ? dcg / ideal : 0;
}
export function median(values: number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}
