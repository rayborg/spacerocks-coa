export const certificateStyleIds = [
  "regal-archive",
  "museum-ledger",
  "celestial-formal",
] as const;

export type CertificateStyleId = (typeof certificateStyleIds)[number];

export interface CertificateStyle {
  id: CertificateStyleId;
  name: string;
  description: string;
}

export const certificateStyles: readonly CertificateStyle[] = [
  {
    id: "regal-archive",
    name: "Regal Archive",
    description: "Double rules, formal hierarchy, and restrained ceremonial corners.",
  },
  {
    id: "museum-ledger",
    name: "Museum Ledger",
    description: "Crisp institutional rules, archival spacing, and minimal ornament.",
  },
  {
    id: "celestial-formal",
    name: "Celestial Formal",
    description: "Subtle orbital geometry with a refined astronomical character.",
  },
];

export function getCertificateStyle(id: CertificateStyleId): CertificateStyle {
  return certificateStyles.find((style) => style.id === id) ?? certificateStyles[0];
}
