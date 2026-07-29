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
    description: "Midnight observatory blue, bright brass, and warm archival ivory.",
    dark: "#061a33",
    darkSoft: "#123a63",
    accent: "#b87518",
    accentLight: "#f2c66d",
    paper: "#f7f0de",
    ink: "#10233b",
    muted: "#5d6570",
    accentText: "#7e4c09",
  },
  {
    id: "museum-burgundy",
    name: "Museum Burgundy",
    description: "Ox-blood red, burnished copper-gold, and rose cream.",
    dark: "#400817",
    darkSoft: "#731c32",
    accent: "#a8501c",
    accentLight: "#f2b96b",
    paper: "#faeee4",
    ink: "#32131c",
    muted: "#715b61",
    accentText: "#7d3510",
  },
  {
    id: "field-archive",
    name: "Field Archive",
    description: "Botanical green, survey brass, and herbarium paper.",
    dark: "#123728",
    darkSoft: "#285d44",
    accent: "#85751a",
    accentLight: "#d9cf72",
    paper: "#eef1df",
    ink: "#163126",
    muted: "#5c6b60",
    accentText: "#5c530c",
  },
  {
    id: "charcoal-gold",
    name: "Charcoal Gold",
    description: "Gallery charcoal, vivid gilt, and limestone paper.",
    dark: "#17191d",
    darkSoft: "#363b44",
    accent: "#c39a32",
    accentLight: "#efd177",
    paper: "#f2efe7",
    ink: "#191c20",
    muted: "#63676d",
    accentText: "#765807",
  },
  {
    id: "desert-copper",
    name: "Desert Copper",
    description: "Canyon umber, bright copper, and sandstone paper.",
    dark: "#542011",
    darkSoft: "#82402a",
    accent: "#bd5b23",
    accentLight: "#f0aa69",
    paper: "#f8e5cc",
    ink: "#3c2116",
    muted: "#795e50",
    accentText: "#8e3d15",
  },
  {
    id: "celestial-teal",
    name: "Celestial Teal",
    description: "Abyssal teal, solar amber, and cool mineral paper.",
    dark: "#00383f",
    darkSoft: "#00606a",
    accent: "#bf7a18",
    accentLight: "#f2bd62",
    paper: "#e7f4f1",
    ink: "#093238",
    muted: "#506b68",
    accentText: "#80500d",
  },
  {
    id: "royal-amethyst",
    name: "Royal Amethyst",
    description: "Saturated amethyst, royal gold, and lavender parchment.",
    dark: "#2d0e52",
    darkSoft: "#57337b",
    accent: "#a96919",
    accentLight: "#e9bc67",
    paper: "#f3eafa",
    ink: "#2d1838",
    muted: "#6c6072",
    accentText: "#77470a",
  },
  {
    id: "arctic-slate",
    name: "Arctic Slate",
    description: "Polar blue slate, weathered bronze, and frost-white paper.",
    dark: "#173549",
    darkSoft: "#315c70",
    accent: "#89641d",
    accentLight: "#dfc27c",
    paper: "#eaf4f7",
    ink: "#18323d",
    muted: "#526a73",
    accentText: "#61440b",
  },
  {
    id: "monochrome-ink",
    name: "Monochrome Ink",
    description: "Near-black ink, cool pewter, and neutral conservation paper.",
    dark: "#101112",
    darkSoft: "#323538",
    accent: "#5b6065",
    accentLight: "#d6d8da",
    paper: "#f6f6f3",
    ink: "#151617",
    muted: "#626465",
    accentText: "#444749",
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
