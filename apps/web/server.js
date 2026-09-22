import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
const root = process.cwd();
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
createServer(async (request, response) => { const path = request.url === "/" ? "/index.html" : request.url; const file = normalize(join(root, path)); if (!file.startsWith(root)) return response.writeHead(403).end(); try { response.writeHead(200, { "content-type": types[extname(file)] || "application/octet-stream" }); response.end(await readFile(file)); } catch { response.writeHead(404).end("Not found"); } }).listen(5173, () => console.log("Live Make console: http://localhost:5173"));
