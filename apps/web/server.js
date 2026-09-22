import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
const root = process.cwd();
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
const apiOrigin = process.env.BUSINESS_API_ORIGIN || "http://127.0.0.1:8080";
createServer(async (request, response) => {
  if (request.url.startsWith("/api/")) {
    try {
      const body = ["GET", "HEAD"].includes(request.method) ? undefined : await new Promise((resolve, reject) => { const chunks = []; request.on("data", (chunk) => chunks.push(chunk)); request.on("end", () => resolve(Buffer.concat(chunks))); request.on("error", reject); });
      const upstream = await fetch(`${apiOrigin}${request.url}`, { method: request.method, headers: { authorization: request.headers.authorization || "", "content-type": request.headers["content-type"] || "application/json" }, body });
      response.writeHead(upstream.status, { "content-type": upstream.headers.get("content-type") || "application/json" });
      response.end(Buffer.from(await upstream.arrayBuffer()));
    } catch { response.writeHead(502, { "content-type": "application/json" }).end(JSON.stringify({ error: "business API unavailable" })); }
    return;
  }
  const path = request.url === "/" ? "/index.html" : request.url;
  const file = normalize(join(root, path));
  if (!file.startsWith(root)) return response.writeHead(403).end();
  try { response.writeHead(200, { "content-type": types[extname(file)] || "application/octet-stream" }); response.end(await readFile(file)); } catch { response.writeHead(404).end("Not found"); }
}).listen(5173, () => console.log("Live Make console: http://localhost:5173"));
