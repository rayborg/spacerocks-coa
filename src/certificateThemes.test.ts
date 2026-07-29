import { describe, expect, it } from "vitest";
import {
  certificateFooterLayout,
  certificateThemeIds,
  certificateThemes,
  getCertificateTheme,
} from "./certificateThemes";

function relativeLuminance(hex: string): number {
  const channels = hex
    .slice(1)
    .match(/.{2}/g)!
    .map((channel) => Number.parseInt(channel, 16) / 255)
    .map((channel) => (channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4));
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

function contrastRatio(left: string, right: string): number {
  const values = [relativeLuminance(left), relativeLuminance(right)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

describe("certificate themes", () => {
  it("provides nine unique selectable palettes", () => {
    expect(certificateThemes).toHaveLength(9);
    expect(new Set(certificateThemeIds).size).toBe(9);
    expect(getCertificateTheme("museum-burgundy").name).toBe("Museum Burgundy");
  });

  it("keeps every major palette channel independently recognizable", () => {
    for (const channel of ["dark", "darkSoft", "accent", "accentLight", "paper"] as const) {
      expect(new Set(certificateThemes.map((theme) => theme[channel])).size, `${channel} values`).toBe(9);
    }

    const signatures = certificateThemes.map(({ dark, accent, paper }) => `${dark}|${accent}|${paper}`);
    expect(new Set(signatures).size).toBe(certificateThemes.length);
    expect(certificateThemes.every(({ dark, accent, paper }) => dark !== accent && accent !== paper)).toBe(true);
  });

  it("keeps primary paper and header text combinations readable", () => {
    for (const theme of certificateThemes) {
      expect(contrastRatio(theme.ink, theme.paper), `${theme.name} paper contrast`).toBeGreaterThanOrEqual(7);
      expect(contrastRatio(theme.accentLight, theme.dark), `${theme.name} header contrast`).toBeGreaterThanOrEqual(4.5);
      expect(contrastRatio(theme.muted, theme.paper), `${theme.name} muted contrast`).toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe("certificate footer layout", () => {
  it("keeps the fingerprint text clear of the inner border", () => {
    expect(certificateFooterLayout.recordHashBaseline).toBeLessThan(certificateFooterLayout.keyFingerprintBaseline);
    expect(certificateFooterLayout.keyFingerprintBaseline + certificateFooterLayout.fontSize)
      .toBeLessThanOrEqual(certificateFooterLayout.innerBorderBottom);
  });
});
