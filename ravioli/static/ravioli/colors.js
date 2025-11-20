// Convert RGB to HSL

 function jetColor(t) {
  const r = Math.floor(255 * Math.min(Math.max(1.5 - Math.abs(4*t - 3), 0), 1));
  const g = Math.floor(255 * Math.min(Math.max(1.5 - Math.abs(4*t - 2), 0), 1));
  const b = Math.floor(255 * Math.min(Math.max(1.5 - Math.abs(4*t - 1), 0), 1));
  return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
}

function rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  let h, s, l = (max + min) / 2;

  if (max === min) {
    h = s = 0; // achromatic
  } else {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch(max) {
      case r: h = (g - b) / d + (g < b ? 6 : 0); break;
      case g: h = (b - r) / d + 2; break;
      case b: h = (r - g) / d + 4; break;
    }
    h /= 6;
  }
  return [h, s, l];
}
alert("HUUUUUJ")
// Convert HSL back to hex
function hslToHex(h, s, l) {
  function hue2rgb(p, q, t) {
    if(t < 0) t += 1;
    if(t > 1) t -= 1;
    if(t < 1/6) return p + (q - p) * 6 * t;
    if(t < 1/2) return q;
    if(t < 2/3) return p + (q - p) * (2/3 - t) * 6;
    return p;
  }
  let r, g, b;
  if(s === 0){
    r = g = b = l; // achromatic
  } else {
    const q = l < 0.5 ? l * (1 + s) : l + s - l*s;
    const p = 2 * l - q;
    r = hue2rgb(p, q, h + 1/3);
    g = hue2rgb(p, q, h);
    b = hue2rgb(p, q, h - 1/3);
  }
  return "#" + [r, g, b].map(x => {
    const hex = Math.round(x * 255).toString(16);
    return hex.length === 1 ? "0" + hex : hex;
  }).join("");
}

function jetColor(t) {
  const r = Math.floor(255 * Math.min(Math.max(1.5 - Math.abs(4*t - 3), 0), 1));
  const g = Math.floor(255 * Math.min(Math.max(1.5 - Math.abs(4*t - 2), 0), 1));
  const b = Math.floor(255 * Math.min(Math.max(1.5 - Math.abs(4*t - 1), 0), 1));
  return [r, g, b];
}

function adjustColor([r, g, b], darkMode, saturationFactor = 1) {
  if (!darkMode) {
    // lighten → blend with white
    r = Math.floor((r + 255) / 2);
    g = Math.floor((g + 255) / 2);
    b = Math.floor((b + 255) / 2);
  } else {
    // darken → blend with black (smoked)
    r = Math.floor(r * 0.6);
    g = Math.floor(g * 0.6);
    b = Math.floor(b * 0.6);
  }

  // Convert to HSL and adjust saturation
  let [h, s, l] = rgbToHsl(r, g, b);
  s *= saturationFactor;
  s = Math.max(0, Math.min(1, s)); // clamp

  return hslToHex(h, s, l);
}