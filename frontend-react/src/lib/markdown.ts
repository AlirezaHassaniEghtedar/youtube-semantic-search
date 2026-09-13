// Markdown subset renderer — direct port of renderMarkdown() in frontend/app.js.
// It escapes HTML FIRST (same approach as the vanilla version), then applies the
// lightweight transformations. Because the input is escaped before any tags are
// produced, the result is safe to inject via dangerouslySetInnerHTML in React:
// model-generated content can never introduce raw HTML, only the few tags the
// regexes below emit.

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
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

  // Line breaks and lists
  const lines = html.split("\n");
  let inUl = false;
  let inOl = false;
  const output: string[] = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();

    if (/^[-•*] /.test(line)) {
      if (!inUl) {
        output.push("<ul>");
        inUl = true;
      }
      output.push("<li>" + line.replace(/^[-•*] /, "") + "</li>");
    } else if (/^\d+\. /.test(line)) {
      if (!inOl) {
        output.push("<ol>");
        inOl = true;
      }
      output.push("<li>" + line.replace(/^\d+\. /, "") + "</li>");
    } else {
      if (inUl) {
        output.push("</ul>");
        inUl = false;
      }
      if (inOl) {
        output.push("</ol>");
        inOl = false;
      }
      if (line) output.push(line);
    }
  }

  if (inUl) output.push("</ul>");
  if (inOl) output.push("</ol>");

  return output.join("\n");
}
