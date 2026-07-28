function parseSseBlock(block) {
  if (!block) {
    return null;
  }

  let event = "message";
  const dataLines = [];
  block.split("\n").forEach((line) => {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim() || "message";
      return;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  });

  if (!dataLines.length) {
    return null;
  }

  const dataText = dataLines.join("\n");
  try {
    return { event, data: JSON.parse(dataText) };
  } catch (error) {
    return { event, data: { message: dataText } };
  }
}

window.parseSseBlock = parseSseBlock;
