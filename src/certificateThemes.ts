export const certificateThemeIds = [
  "observatory-navy",
  "museum-burgundy",
  "field-archive",
  "charcoal-gold",
  "desert-copper",
  "celestial-teal",
  "royal-amethyst",
  "arctic-slate",
  "monochrome-ink",
] as const;

export type CertificateThemeId = (typeof certificateThemeIds)[number];

export interface CertificateTheme {
  id: CertificateThemeId;
  name: string;
  description: string;
  dark: string;
  darkSoft: string;
  accent: string;
  accentLight: string;
  paper: string;
  ink: string;
  muted: string;
  accentText: string;
}

export const certificateThemes: readonly CertificateTheme[] = [
  {
    id: "observatory-navy",
    name: "Observatory Navy",
    description: "Deep navy, brass, and warm archival ivory.",
    dark: "#071a2f",
    darkSoft: "#102b48",
    accent: "#b9852e",
    accentLight: "#e3bc69",
    paper: "#f5f0e3",
    ink: "#122236",
    muted: "#5d6874",
    accentText: "#805916",
  },
  {
    id: "museum-burgundy",
    name: "Museum Burgundy",
    description: "Ox-blood red with antique gold and cream.",
    dark: "#3b0d18",
    darkSoft: "#5a1a2a",
    accent: "#a66d2c",
    accentLight: "#e4bc78",
    paper: "#f7f0e5",
    ink: "#2c1820",
    muted: "#715f63",
    accentText: "#7a4912",
  },
  {
    id: "field-archive",
    name: "Field Archive",
    description: "Mineral green, aged brass, and field-paper ivory.",
    dark: "#17382e",
    darkSoft: "#285044",
    accent: "#a4772d",
    accentLight: "#dfc27b",
    paper: "#f3f0e3",
    ink: "#173128",
    muted: "#61716a",
    accentText: "#735116",
  },
  {
    id: "charcoal-gold",
    name: "Charcoal Gold",
    description: "Graphite black with restrained gallery gold.",
    dark: "#20242a",
    darkSoft: "#363c43",
    accent: "#b58a3c",
    accentLight: "#e4c878",
    paper: "#f3f0e8",
    ink: "#252a30",
    muted: "#686e75",
    accentText: "#765614",
  },
  {
    id: "desert-copper",
    name: "Desert Copper",
    description: "Burnished umber, copper, and sandstone paper.",
    dark: "#4a2417",
    darkSoft: "#6b3827",
    accent: "#b76635",
    accentLight: "#e5a66f",
    paper: "#f7eddc",
    ink: "#352219",
    muted: "#78685e",
    accentText: "#88401c",
  },
  {
    id: "celestial-teal",
    name: "Celestial Teal",
    description: "Deep teal, muted gold, and cool mineral paper.",
    dark: "#06383d",
    darkSoft: "#12545b",
    accent: "#b17d36",
    accentLight: "#e0bd75",
    paper: "#eef4ee",
    ink: "#123438",
    muted: "#586b6e",
    accentText: "#805415",
  },
  {
    id: "royal-amethyst",
    name: "Royal Amethyst",
    description: "Dark violet, old gold, and pale parchment.",
    dark: "#2b174a",
    darkSoft: "#463067",
    accent: "#a77b39",
    accentLight: "#dfc07d",
    paper: "#f4f0ea",
    ink: "#281e35",
    muted: "#706779",
    accentText: "#755216",
  },
  {
    id: "arctic-slate",
    name: "Arctic Slate",
    description: "Blue slate, soft bronze, and frost-white paper.",
    dark: "#243746",
    darkSoft: "#3b5363",
    accent: "#8a6b38",
    accentLight: "#d8c18b",
    paper: "#f0f4f3",
    ink: "#203039",
    muted: "#5d6c72",
    accentText: "#674b1c",
  },
  {
    id: "monochrome-ink",
    name: "Monochrome Ink",
    description: "Black, pewter, and neutral conservation paper.",
    dark: "#161616",
    darkSoft: "#303030",
    accent: "#62625d",
    accentLight: "#d8d8d0",
    paper: "#f5f5ef",
    ink: "#161616",
    muted: "#626262",
    accentText: "#444440",
  },
];

export function getCertificateTheme(id: CertificateThemeId): CertificateTheme {
  return certificateThemes.find((theme) => theme.id === id) ?? certificateThemes[0];
}

export const certificateFooterLayout = {
  innerBorderBottom: 1658,
  recordHashBaseline: 1608,
  keyFingerprintBaseline: 1638,
  fontSize: 16,
} as const;
