// Markdown subset renderer — direct port of renderMarkdown() in frontend/app.js.
// It escapes HTML FIRST (same approach as the vanilla version), then applies the
// lightweight transformations. Because the input is escaped before any tags are
// produced, the result is safe to inject via dangerouslySetInnerHTML in React:
// model-generated content can never introduce raw HTML, only the few tags the
// regexes below emit.
//
// Line processing works on BLOCKS separated by blank lines (standard paragraph
// semantics):
//   - a block of list-item lines emits a <ul>/<ol>, exactly as before;
//   - a prose block (possibly several consecutive lines) is wrapped in a single
//     <p>, with internal line breaks preserved as <br/> (soft wrap within the
//     same paragraph).
// Joining blocks with "\n" used to be enough for <pre> contexts, but in normal
// HTML a bare newline collapses to a space, so multiple paragraphs rendered as
// one unbroken blob. Wrapping each block in <p>/<ul>/<ol> lets the CSS margins
// on .chat-message__text create real visual separation between blocks.

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const LIST_ITEM_RE = /^(\d+)\. /;

function renderListBlock(block: string[], ordered: boolean): string {
  const open = ordered ? "<ol>" : "<ul>";
  const close = ordered ? "</ol>" : "</ul>";
  const items = block.map((line) => {
    const content = ordered ? line.replace(LIST_ITEM_RE, "") : line.replace(/^[-•*] /, "");
    return "<li>" + content + "</li>";
  });
  return open + "\n" + items.join("\n") + "\n" + close;
}

export function renderMarkdown(text: string): string {
  let html = escapeHtml(text);

  // Bold: **text** or __text__
  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__(.*?)__/g, "<strong>$1</strong>");

  // Italic: *text* or _text_
  html = html.replace(/\*(.*?)\*/g, "<em>$1</em>");
  html = html.replace(/_(.*?)_/g, "<em>$1</em>");

  // Inline code: `text`
  html = html.replace(/`(.*?)`/g, "<code>$1</code>");

  const output: string[] = [];

  // Split into blocks on blank lines. Consecutive non-blank lines form ONE
  // block (a paragraph or a list).
  const blocks = html.split(/\n/).reduce<string[][]>((acc, rawLine) => {
    const line = rawLine.trim();
    if (line === "") {
      acc.push([]); // blank line: start a new block
    } else {
      if (acc.length === 0) acc.push([]);
      acc[acc.length - 1].push(line);
    }
    return acc;
  }, []);

  for (const block of blocks) {
    if (block.length === 0) continue; // skip empty blocks from leading/trailing blank lines

    const isUl = /^[-•*] /.test(block[0]);
    const olMatch = block[0].match(LIST_ITEM_RE);
    if (isUl && block.every((line) => /^[-•*] /.test(line))) {
      output.push(renderListBlock(block, false));
    } else if (olMatch && block.every((line) => LIST_ITEM_RE.test(line))) {
      output.push(renderListBlock(block, true));
    } else {
      // Prose block. A list item that appears mid-block (e.g. a list directly
      // followed by prose without a blank line) still gets its marker kept as
      // plain text — the same content the old renderer produced.
      output.push("<p>" + block.join("<br/>") + "</p>");
    }
  }

  return output.join("\n");
}
