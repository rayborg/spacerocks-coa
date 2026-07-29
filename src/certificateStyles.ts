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
    description: "Layered double rules, ceremonial symmetry, and formal corner flourishes.",
  },
  {
    id: "museum-ledger",
    name: "Museum Ledger",
    description: "Light institutional fields, square geometry, and open archival rules.",
  },
  {
    id: "celestial-formal",
    name: "Celestial Formal",
    description: "Orbital star-map geometry, soft panels, and a dynamic astronomical field.",
  },
];

export function getCertificateStyle(id: CertificateStyleId): CertificateStyle {
  return certificateStyles.find((style) => style.id === id) ?? certificateStyles[0];
}
