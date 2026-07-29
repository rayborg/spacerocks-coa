export const certificateStyleIds = [
  "regal-archive",
  "museum-ledger",
  "celestial-formal",
  "museum-type",
] as const;

export type CertificateStyleId = (typeof certificateStyleIds)[number];

export interface CertificateStyle {
  id: CertificateStyleId;
  name: string;
  description: string;
}

const legacyCertificateStyles: readonly CertificateStyle[] = [
  {
    id: "regal-archive",
    name: "Regal Archive",
    description: "Engraved keylines, ceremonial cartouches, and balanced archival symmetry.",
  },
  {
    id: "museum-ledger",
    name: "Museum Ledger",
    description: "Accession-grid structure, documentation plates, and institutional catalog rules.",
  },
];

export const certificateStyles: readonly CertificateStyle[] = [
  {
    id: "celestial-formal",
    name: "Celestial Formal",
    description: "Orbital star-map geometry, soft panels, and a dynamic astronomical field.",
  },
  {
    id: "museum-type",
    name: "Museum Type",
    description: "Bold specimen typography, ruled scientific panels, and a modern identification-card frame.",
  },
];

const allCertificateStyles = [...legacyCertificateStyles, ...certificateStyles] as const;

export function getCertificateStyle(id: CertificateStyleId): CertificateStyle {
  return allCertificateStyles.find((style) => style.id === id) ?? certificateStyles[0];
}
