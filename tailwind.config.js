module.exports = {
  darkMode: "class",
  content: ["./index.html"],
  theme: {
    extend: {
      colors: {
        carbon: {
          900: "#0B0D11",
          800: "#12161F",
          700: "#1A202C",
          600: "#2D3748"
        },
        neon: {
          cyan: "#00F0FF",
          amber: "#FFB800",
          crimson: "#FF2A55",
          green: "#00FF66"
        }
      },
      fontFamily: {
        sans: ["Inter", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"]
      }
    }
  },
  plugins: []
};
