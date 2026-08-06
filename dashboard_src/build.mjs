import * as esbuild from "esbuild";

await esbuild.build({
  entryPoints: ["src/main.tsx"],
  bundle: true,
  minify: true,
  format: "iife",
  target: "es2020",
  outfile: "../static/dashboard/som_dashboard.js",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"production"' },
  loader: { ".css": "css" },
  logLevel: "info",
});
console.log("Build OK");
