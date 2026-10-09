export function countVowels(text: string): number {
  return [...text.toLowerCase()].filter(char => "aeiou".includes(char)).length;
}
