export function reverseWords(text: string): string {
  return text.trim().split(/\s+/).filter(Boolean).reverse().join(" ");
}
