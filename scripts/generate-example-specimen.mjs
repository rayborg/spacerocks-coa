import { chromium } from "playwright";
import { writeFile } from "node:fs/promises";

const outputPath = new URL("../src/assets/aster-vale-specimen.jpg", import.meta.url);
const browser = await chromium.launch({
  channel: process.env.CI ? undefined : "chrome",
  headless: true,
});

try {
  const page = await browser.newPage();
  const dataUrl = await page.evaluate(() => {
    const width = 1400;
    const height = 1050;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("Canvas is unavailable");

    const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));
    const smoothstep = (value) => value * value * (3 - 2 * value);
    const hash = (x, y) => {
      let value = Math.imul(x, 374761393) + Math.imul(y, 668265263) + 0x5f356495;
      value = Math.imul(value ^ (value >>> 13), 1274126177);
      return ((value ^ (value >>> 16)) >>> 0) / 4294967295;
    };
    const noise = (x, y) => {
      const x0 = Math.floor(x);
      const y0 = Math.floor(y);
      const tx = smoothstep(x - x0);
      const ty = smoothstep(y - y0);
      const top = hash(x0, y0) * (1 - tx) + hash(x0 + 1, y0) * tx;
      const bottom = hash(x0, y0 + 1) * (1 - tx) + hash(x0 + 1, y0 + 1) * tx;
      return (top * (1 - ty) + bottom * ty) * 2 - 1;
    };
    const fbm = (x, y) => {
      let value = 0;
      let amplitude = 0.54;
      let frequency = 1;
      for (let octave = 0; octave < 5; octave += 1) {
        value += noise(x * frequency, y * frequency) * amplitude;
        frequency *= 2.03;
        amplitude *= 0.48;
      }
      return value;
    };

    const background = context.createRadialGradient(560, 360, 70, 700, 510, 920);
    background.addColorStop(0, "#555b5e");
    background.addColorStop(0.48, "#30363a");
    background.addColorStop(1, "#11161a");
    context.fillStyle = background;
    context.fillRect(0, 0, width, height);

    const backgroundPixels = context.getImageData(0, 0, width, height);
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const index = (y * width + x) * 4;
        const grain = (hash(x, y) - 0.5) * 12;
        const sweep = Math.sin((x + y * 0.45) * 0.017) * 1.4;
        backgroundPixels.data[index] = clamp(backgroundPixels.data[index] + grain + sweep, 0, 255);
        backgroundPixels.data[index + 1] = clamp(backgroundPixels.data[index + 1] + grain, 0, 255);
        backgroundPixels.data[index + 2] = clamp(backgroundPixels.data[index + 2] + grain - sweep, 0, 255);
      }
    }
    context.putImageData(backgroundPixels, 0, 0);

    context.save();
    context.globalAlpha = 0.16;
    context.strokeStyle = "#c3ccd0";
    context.lineWidth = 1;
    for (let x = 90; x <= 1310; x += 122) {
      context.beginPath();
      context.moveTo(x, 118);
      context.lineTo(x, 850);
      context.stroke();
    }
    for (let y = 118; y <= 850; y += 122) {
      context.beginPath();
      context.moveTo(90, y);
      context.lineTo(1310, y);
      context.stroke();
    }
    context.restore();

    const rockCanvas = document.createElement("canvas");
    rockCanvas.width = width;
    rockCanvas.height = height;
    const rockContext = rockCanvas.getContext("2d");
    if (!rockContext) throw new Error("Rock canvas is unavailable");
    const rockImage = rockContext.createImageData(width, height);
    const heightMap = new Float32Array(width * height);
    const textureMap = new Float32Array(width * height);
    const craterMap = new Float32Array(width * height);
    const alphaMap = new Uint8ClampedArray(width * height);

    const centerX = 700;
    const centerY = 472;
    const radiusX = 448;
    const radiusY = 342;
    const rotation = -0.105;
    const cosine = Math.cos(rotation);
    const sine = Math.sin(rotation);
    const craters = [
      [-0.46, -0.28, 0.105, 0.078, 0.12],
      [-0.16, -0.43, 0.072, 0.096, 0.08],
      [0.24, -0.35, 0.13, 0.082, 0.145],
      [0.52, -0.08, 0.087, 0.128, 0.12],
      [-0.3, 0.08, 0.118, 0.072, 0.105],
      [0.05, 0.03, 0.062, 0.052, 0.055],
      [0.35, 0.2, 0.115, 0.077, 0.105],
      [-0.5, 0.34, 0.076, 0.102, 0.09],
      [-0.08, 0.43, 0.092, 0.061, 0.075],
      [0.25, 0.52, 0.064, 0.083, 0.07],
    ];

    const minimumX = 205;
    const maximumX = 1190;
    const minimumY = 92;
    const maximumY = 855;
    for (let y = minimumY; y <= maximumY; y += 1) {
      for (let x = minimumX; x <= maximumX; x += 1) {
        const translatedX = x - centerX;
        const translatedY = y - centerY;
        const normalizedX = (translatedX * cosine + translatedY * sine) / radiusX;
        const normalizedY = (-translatedX * sine + translatedY * cosine) / radiusY;
        const angle = Math.atan2(normalizedY, normalizedX);
        const boundary = 1
          + Math.sin(angle * 5 + 0.7) * 0.052
          + Math.sin(angle * 9 - 1.1) * 0.026
          + Math.sin(angle * 17 + 2.2) * 0.014;
        const radial = Math.hypot(normalizedX, normalizedY) / boundary;
        if (radial >= 1.015) continue;

        const index = y * width + x;
        const edge = clamp((1.015 - radial) * 68, 0, 1);
        const dome = Math.pow(Math.max(0, 1 - radial * radial), 0.34);
        const broadTexture = fbm(normalizedX * 3.4 + 8.3, normalizedY * 3.4 - 2.7);
        const fineTexture = fbm(normalizedX * 13.5 - 4.1, normalizedY * 13.5 + 6.4);
        let craterDepth = 0;
        let craterRim = 0;
        for (const [craterX, craterY, craterRadiusX, craterRadiusY, depth] of craters) {
          const deltaX = (normalizedX - craterX) / craterRadiusX;
          const deltaY = (normalizedY - craterY) / craterRadiusY;
          const distance = Math.hypot(deltaX, deltaY);
          craterDepth += Math.exp(-distance * distance * 1.55) * depth;
          craterRim += Math.exp(-Math.pow(distance - 1.18, 2) * 14) * depth * 0.22;
        }
        const relief = broadTexture * 0.105 + fineTexture * 0.034;
        heightMap[index] = Math.max(0.012, dome * 0.86 + relief * (0.28 + dome * 0.72) - craterDepth + craterRim);
        textureMap[index] = broadTexture * 0.72 + fineTexture * 0.28;
        craterMap[index] = craterDepth;
        alphaMap[index] = Math.round(edge * 255);
      }
    }

    const light = [-0.47, -0.58, 0.665];
    const halfVectorLength = Math.hypot(light[0], light[1], light[2] + 1);
    const halfVector = [light[0] / halfVectorLength, light[1] / halfVectorLength, (light[2] + 1) / halfVectorLength];
    for (let y = minimumY + 1; y < maximumY; y += 1) {
      for (let x = minimumX + 1; x < maximumX; x += 1) {
        const index = y * width + x;
        const alpha = alphaMap[index];
        if (!alpha) continue;
        const horizontalSlope = (heightMap[index + 1] - heightMap[index - 1]) * 13;
        const verticalSlope = (heightMap[index + width] - heightMap[index - width]) * 13;
        const normalLength = Math.hypot(horizontalSlope, verticalSlope, 1);
        const normalX = -horizontalSlope / normalLength;
        const normalY = -verticalSlope / normalLength;
        const normalZ = 1 / normalLength;
        const diffuse = Math.max(0, normalX * light[0] + normalY * light[1] + normalZ * light[2]);
        const specular = Math.pow(Math.max(0, normalX * halfVector[0] + normalY * halfVector[1] + normalZ * halfVector[2]), 30);
        const texture = textureMap[index];
        const craterShade = clamp(craterMap[index] * 1.8, 0, 0.32);
        const crustVariation = (texture + 1) * 0.5;
        const illumination = (0.22 + diffuse * 0.8) * (1 - craterShade);
        const warmEdge = alpha < 245 && diffuse > 0.3 ? 9 : 0;
        const grain = (hash(x * 3, y * 3) - 0.5) * 8;
        const pixel = index * 4;
        rockImage.data[pixel] = clamp((30 + crustVariation * 25) * illumination + specular * 80 + warmEdge + grain, 0, 255);
        rockImage.data[pixel + 1] = clamp((25 + crustVariation * 17) * illumination + specular * 57 + warmEdge * 0.55 + grain * 0.6, 0, 255);
        rockImage.data[pixel + 2] = clamp((22 + crustVariation * 12) * illumination + specular * 40 + warmEdge * 0.25 + grain * 0.35, 0, 255);
        rockImage.data[pixel + 3] = alpha;

        if (hash(x * 7 + 19, y * 11 - 31) > 0.9982 && craterMap[index] < 0.02) {
          rockImage.data[pixel] = 151;
          rockImage.data[pixel + 1] = 123;
          rockImage.data[pixel + 2] = 91;
        }
      }
    }
    rockContext.putImageData(rockImage, 0, 0);

    const shadowCanvas = document.createElement("canvas");
    shadowCanvas.width = width;
    shadowCanvas.height = height;
    const shadowContext = shadowCanvas.getContext("2d");
    if (!shadowContext) throw new Error("Shadow canvas is unavailable");
    shadowContext.drawImage(rockCanvas, 0, 0);
    shadowContext.globalCompositeOperation = "source-in";
    shadowContext.fillStyle = "#000000";
    shadowContext.fillRect(0, 0, width, height);
    context.save();
    context.filter = "blur(34px)";
    context.globalAlpha = 0.74;
    context.drawImage(shadowCanvas, 27, 38);
    context.restore();
    context.drawImage(rockCanvas, 0, 0);

    const flowCanvas = document.createElement("canvas");
    flowCanvas.width = width;
    flowCanvas.height = height;
    const flowContext = flowCanvas.getContext("2d");
    if (!flowContext) throw new Error("Flow canvas is unavailable");
    flowContext.lineCap = "round";
    flowContext.lineWidth = 4;
    flowContext.strokeStyle = "rgba(157, 127, 94, 0.16)";
    for (const points of [
      [405, 356, 545, 266, 733, 276, 868, 329],
      [363, 500, 518, 430, 719, 438, 880, 492],
      [430, 654, 587, 610, 785, 626, 922, 570],
      [548, 754, 676, 703, 818, 710, 915, 650],
    ]) {
      flowContext.beginPath();
      flowContext.moveTo(points[0], points[1]);
      flowContext.bezierCurveTo(points[2], points[3], points[4], points[5], points[6], points[7]);
      flowContext.stroke();
    }
    flowContext.globalCompositeOperation = "destination-in";
    flowContext.drawImage(rockCanvas, 0, 0);
    context.drawImage(flowCanvas, 0, 0);

    context.fillStyle = "rgba(8, 14, 18, 0.82)";
    context.fillRect(42, 34, 495, 70);
    context.strokeStyle = "rgba(217, 192, 157, 0.48)";
    context.strokeRect(42.5, 34.5, 495, 70);
    context.fillStyle = "#e6d2b5";
    context.font = "700 18px Arial, sans-serif";
    context.fillText("ASTER VALE 001 / SYNTHETIC DEMO", 64, 63);
    context.fillStyle = "#9eabb2";
    context.font = "13px Arial, sans-serif";
    context.fillText("AI-GENERATED SPECIMEN IMAGE - NOT A REAL PHOTOGRAPH", 64, 86);

    context.save();
    context.translate(445, 955);
    context.strokeStyle = "#e6d2b5";
    context.fillStyle = "#e6d2b5";
    context.lineWidth = 3;
    context.beginPath();
    context.moveTo(0, 0);
    context.lineTo(510, 0);
    context.stroke();
    for (let tick = 0; tick <= 10; tick += 1) {
      const x = tick * 51;
      context.beginPath();
      context.moveTo(x, -10);
      context.lineTo(x, tick % 5 === 0 ? 18 : 10);
      context.stroke();
    }
    context.font = "700 14px Arial, sans-serif";
    context.fillText("0", -4, 40);
    context.fillText("50 MM", 462, 40);
    context.restore();

    const chips = ["#ece6db", "#9b734d", "#58626a", "#252a2d", "#111315"];
    chips.forEach((color, index) => {
      context.fillStyle = color;
      context.fillRect(1130 + index * 43, 928, 32, 32);
    });
    context.fillStyle = "#a9b4bb";
    context.font = "12px Arial, sans-serif";
    context.textAlign = "right";
    context.fillText("DEMO COLOR REFERENCE", 1358, 985);
    context.textAlign = "left";

    const vignette = context.createRadialGradient(700, 500, 410, 700, 500, 810);
    vignette.addColorStop(0, "rgba(0,0,0,0)");
    vignette.addColorStop(1, "rgba(0,0,0,0.48)");
    context.fillStyle = vignette;
    context.fillRect(0, 0, width, height);

    return canvas.toDataURL("image/jpeg", 0.94);
  });

  const encoded = dataUrl.slice(dataUrl.indexOf(",") + 1);
  await writeFile(outputPath, Buffer.from(encoded, "base64"));
  console.log(`Generated ${outputPath.pathname}`);
} finally {
  await browser.close();
}
