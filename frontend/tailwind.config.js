/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}"
  ],
  theme: {
    extend: {
      colors: {
        ink: "#0E0F13",
        frost: "#E6E7EB",
        ember: "#F25F5C",
        neon: "#86F2B7",
        haze: "#22252B"
      }
    }
  },
  plugins: []
};
