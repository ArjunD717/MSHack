import fs from 'node:fs/promises';
import { FileBlob, PresentationFile } from 'file:///C:/Users/2007r/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs';
const p = await PresentationFile.importPptx(await FileBlob.load('C:/Users/2007r/Downloads/Microsoft x LSEG Hackathon.pptx'));
for (let i=0;i<p.slides.items.length;i++) {
 const png=await p.export({slide:p.slides.items[i],format:'png',scale:1});
 await fs.writeFile(`.deck-review/slide-${i+1}.png`,new Uint8Array(await png.arrayBuffer()));
}
console.log('Rendered',p.slides.items.length);

