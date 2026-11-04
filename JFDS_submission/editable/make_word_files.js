// Builds the three Word files for the JFDS submission:
//   1_Cover_Letter.docx, 2_Title_Page_with_Author_Details.docx,
//   4_Declaration_of_Competing_Interests.docx
// Usage: node make_word_files.js <output-directory>
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, AlignmentType, HeadingLevel,
  ExternalHyperlink, CheckBox, Table, TableRow, TableCell, WidthType,
  BorderStyle, LevelFormat, TabStopType, Tab,
} = require("docx");

const OUT = process.argv[2] || ".";
fs.mkdirSync(OUT, { recursive: true });

const TITLE = "Beyond Directional Accuracy: A Verified Walk-Forward Framework for " +
  "Machine-Learning Cryptocurrency Trading";
const JOURNAL = "The Journal of Finance and Data Science";
const REPO = "https://github.com/Ratnachand04/FINBIN";
const A1 = {
  name: "Ratnachand Kancharla", email: "ratnachandkancharla@gmail.com",
  orcid: "0009-0003-5634-8209", aff: "NMIMS Hyderabad Campus, Hyderabad, Telangana, India",
};
const A2 = {
  name: "Sai Srinivas Panthangi", email: "saisrinivaspanthangi09@gmail.com",
  orcid: "0009-0009-2363-4667", aff: "Malla Reddy University, Hyderabad, Telangana 500100, India",
};

// A4 page, 2.54 cm margins; text width = 11906 - 2*1440 = 9026 DXA
const PAGE = {
  size: { width: 11906, height: 16838 },
  margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
};
const TEXT_WIDTH = 9026;
const FONT = "Times New Roman";

const styles = {
  default: {
    document: { run: { font: FONT, size: 24 }, paragraph: { spacing: { line: 276, after: 120 } } },
    title: { run: { font: FONT, size: 30, bold: true },
      paragraph: { alignment: AlignmentType.CENTER, spacing: { before: 0, after: 240 }, keepNext: true } },
    heading1: { run: { font: FONT, size: 24, bold: true },
      paragraph: { spacing: { before: 240, after: 80 }, keepNext: true } },
  },
};
// The cover letter uses 11 pt and 2 cm top/bottom margins so that it fits on one page.
const coverStyles = {
  default: {
    document: { run: { font: FONT, size: 22 }, paragraph: { spacing: { line: 264, after: 100 } } },
    title: styles.default.title,
    heading1: styles.default.heading1,
  },
};
const COVER_PAGE = {
  size: { width: 11906, height: 16838 },
  margin: { top: 1134, right: 1304, bottom: 1134, left: 1304 },
};
const numbering = {
  config: [{
    reference: "bullets",
    levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 720, hanging: 360 } } } }],
  }],
};

const t = (text, opts = {}) => new TextRun({ text, ...opts });
const p = (children, opts = {}) => new Paragraph({
  children: (Array.isArray(children) ? children : [children]).map((c) => (typeof c === "string" ? t(c) : c)),
  ...opts,
});
const just = (children, opts = {}) => p(children, { alignment: AlignmentType.JUSTIFIED, ...opts });
const h1 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [t(text)] });
const bullet = (children) => p(children, { numbering: { reference: "bullets", level: 0 }, alignment: AlignmentType.JUSTIFIED, spacing: { after: 60 } });
const link = (url, label) => new ExternalHyperlink({ link: url,
  children: [t(label || url, { color: "0563C1", underline: {} })] });
const tight = { spacing: { after: 0 } };

async function save(doc, name) {
  const buf = await Packer.toBuffer(doc);
  fs.writeFileSync(path.join(OUT, name), buf);
  console.log("wrote", name);
}

// ---------------------------------------------------------------- cover letter
function coverLetter() {
  const children = [
    p("4 November 2026", { spacing: { after: 200 } }),
    p("Professor Jing-Zhi Huang", tight),
    p("Editor-in-Chief", tight),
    p(t(JOURNAL, { italics: true }), { spacing: { after: 200 } }),
    p([t("Subject: ", { bold: true }), t("Submission of a research article for consideration")],
      { spacing: { after: 200 } }),
    p("Dear Professor Huang,", { spacing: { after: 120 } }),
    just([
      t("We would like to submit our manuscript, “"), t(TITLE, { italics: true }),
      t("”, for consideration as a research article in "), t(JOURNAL, { italics: true }), t("."),
    ]),
    just("Many studies that use machine learning to predict cryptocurrency prices report only " +
      "directional accuracy. In our work we saw that accuracy alone can give a wrong picture of a " +
      "model. So we built a step-by-step evaluation framework that connects forecasting, " +
      "statistical testing and trading costs, and that also checks the backtest code itself with " +
      "tests. We used it on daily Binance data for Bitcoin, Ether and Dogecoin, with 5,562 " +
      "out-of-sample forecasts from July 2020 to March 2026."),
    just("The main findings are:", { spacing: { after: 60 } }),
    bullet("A regularized logistic regression model is correct 52.95% of the time, and it is " +
      "better than a coin flip on all three assets."),
    bullet("A simple one-day reversal rule gets almost the same accuracy, but the model is better " +
      "on the large price moves. Its long/short portfolio earns a gross Sharpe ratio of 0.54, " +
      "compared with 0.004 for the reversal rule."),
    bullet("With a cost of 9 basis points per unit of turnover, the net Sharpe ratio stays " +
      "positive (0.11), and the break-even cost is 11.4 basis points."),
    bullet("The tests of the backtest engine caught all eight rule violations that we put back " +
      "into the code on purpose."),
    just("We think the paper fits the aims and scope of the journal well. It uses data science " +
      "methods for a finance problem, and it gives a practical method that other researchers can " +
      "use and check. All forecasts are saved with their dates, and the full analysis can be run " +
      "again with one command. The journal has published related work on bitcoin prediction with " +
      "machine learning (Jaquart et al., 2021), and our paper adds to this line of work with an " +
      "evaluation that takes simple baselines, the size of price moves and trading costs into " +
      "account.", { spacing: { before: 120, after: 120 } }),
    just("We confirm that this manuscript is our own original work. It has not been published " +
      "before, and it is not under consideration at any other journal. Both authors have read and " +
      "approved the final version and agree with this submission. The authors have no competing " +
      "interests, and this research did not receive any specific funding. The study uses only " +
      "public market data, so ethics approval was not needed. We used AI tools in some parts of " +
      "the work, and we explain this in the declaration of generative AI at the end of the " +
      "manuscript."),
    just("Because the journal uses double-anonymized peer review, the manuscript file does not show " +
      "the names of the authors. The author details, the declarations and the link to the code " +
      "and data are given on the separate title page."),
    just("Thank you for your time and for considering our manuscript. We look forward to hearing " +
      "from you."),
    p("Yours sincerely,", { spacing: { before: 160, after: 320 } }),
    p(t(A1.name, { bold: true }), tight),
    p("Corresponding author, on behalf of both authors", tight),
    p(A1.aff, tight),
    p([t("Email: "), link("mailto:" + A1.email, A1.email)], tight),
    p([t("ORCID: "), link("https://orcid.org/" + A1.orcid, A1.orcid)], { spacing: { after: 120 } }),
    p([t("Co-author: ", { bold: true }), t(`${A2.name}, ${A2.aff}`)], tight),
  ];
  return new Document({
    creator: A1.name, lastModifiedBy: A1.name, title: "Cover letter",
    styles: coverStyles, numbering,
    sections: [{ properties: { page: COVER_PAGE }, children }],
  });
}

// ------------------------------------------------------------------ title page
function titlePage() {
  const aff = (sup, text) => p([t(sup, { superScript: true }), t(" " + text)], { spacing: { after: 40 }, alignment: AlignmentType.CENTER });
  const children = [
    p(t("Title page", { bold: true, size: 22, color: "555555" }), { alignment: AlignmentType.CENTER, spacing: { after: 200 } }),
    new Paragraph({ heading: HeadingLevel.TITLE, children: [t(TITLE)] }),
    p([t(A1.name), t("a,", { superScript: true }), t("*", { superScript: true }),
       t(" and "), t(A2.name), t("b", { superScript: true })],
      { alignment: AlignmentType.CENTER, spacing: { after: 160 } }),
    aff("a", A1.aff),
    aff("b", A2.aff),
    p(" ", { spacing: { after: 120 } }),
    p([t("Article type: ", { bold: true }), t("Research article")], tight),
    p([t("Journal: ", { bold: true }), t(JOURNAL, { italics: true })], { spacing: { after: 120 } }),

    h1("* Corresponding author"),
    p(A1.name, tight),
    p(A1.aff, tight),
    p([t("Email: "), link("mailto:" + A1.email, A1.email)], tight),
    p([t("ORCID: "), link("https://orcid.org/" + A1.orcid, "https://orcid.org/" + A1.orcid)]),

    h1("Author details"),
    p([t(A1.name, { bold: true }), t(` (corresponding author), ${A1.aff}. Email: ${A1.email}. ORCID: ${A1.orcid}.`)],
      { alignment: AlignmentType.JUSTIFIED }),
    p([t(A2.name, { bold: true }), t(`, ${A2.aff}. Email: ${A2.email}. ORCID: ${A2.orcid}.`)],
      { alignment: AlignmentType.JUSTIFIED }),

    h1("Keywords"),
    p("Financial machine learning; Walk-forward validation; Cryptocurrency; Forecast evaluation; " +
      "Transaction costs; Backtest verification"),
    p([t("JEL classification: ", { bold: true }), t("G17, C53, C58, G14")]),

    h1("CRediT authorship contribution statement"),
    just([t(A1.name + ": ", { bold: true }), t("Conceptualization, Formal analysis, Investigation, " +
      "Methodology, Project administration, Resources, Software, Supervision, Validation, " +
      "Writing – review & editing.")]),
    just([t(A2.name + ": ", { bold: true }), t("Software, Visualization, Writing – original draft, " +
      "Writing – review & editing.")]),

    h1("Declaration of competing interest"),
    just("The authors declare that they have no known competing financial interests or personal " +
      "relationships that could have appeared to influence the work reported in this paper."),

    h1("Funding"),
    just("This research did not receive any specific grant from funding agencies in the public, " +
      "commercial, or not-for-profit sectors."),

    h1("Acknowledgements"),
    p("None."),

    h1("Data and code availability"),
    just([t("The source code, the three input files, the dated out-of-sample forecasts, the trade " +
      "ledger and all machine-readable results are available at "), link(REPO),
      t(". The market data are public Binance kline archives. The full analysis is rebuilt by " +
      "the command python scripts/reproduce_jfds.py.")]),

    h1("Declaration of generative AI and AI-assisted technologies"),
    just("During the preparation of this work the authors used OpenAI Codex and Anthropic Claude to " +
      "help with parts of the analysis code and tests, the search for related literature, and the " +
      "writing and checking of the manuscript. No data and no reported number was produced by a " +
      "language model. After using these tools, the authors reviewed and edited the content as " +
      "needed and take full responsibility for the content of the publication. The full statement " +
      "is given at the end of the manuscript."),

    h1("Ethics statement"),
    just("The study uses only public market data and software written by the authors. It does not " +
      "involve human participants, personal data or animals, so ethics approval and informed " +
      "consent were not needed."),
  ];
  return new Document({
    creator: A1.name, lastModifiedBy: A1.name, title: "Title page",
    styles, numbering,
    sections: [{ properties: { page: PAGE }, children }],
  });
}

// ------------------------------------------- declaration of competing interests
function declaration() {
  const box = (checked) => new CheckBox({
    checked,
    checkedState: { value: "2612", font: "MS Gothic" },
    uncheckedState: { value: "2610", font: "MS Gothic" },
  });
  const option = (checked, text) => new Paragraph({
    tabStops: [{ type: TabStopType.LEFT, position: 567 }],
    indent: { left: 567, hanging: 567 },
    alignment: AlignmentType.JUSTIFIED,
    spacing: { before: 120, after: 200 },
    children: [box(checked), new TextRun({ children: [new Tab(), text] })],
  });
  const border = { style: BorderStyle.SINGLE, size: 6, color: "808080" };
  const emptyBox = new Table({
    width: { size: TEXT_WIDTH, type: WidthType.DXA },
    columnWidths: [TEXT_WIDTH],
    rows: [new TableRow({
      height: { value: 2000, rule: "atLeast" },
      children: [new TableCell({
        width: { size: TEXT_WIDTH, type: WidthType.DXA },
        borders: { top: border, bottom: border, left: border, right: border },
        margins: { top: 120, bottom: 120, left: 120, right: 120 },
        children: [p(t("Not applicable.", { italics: true, color: "555555" }))],
      })],
    })],
  });
  const children = [
    new Paragraph({ heading: HeadingLevel.TITLE, children: [t("Declaration of interests")] }),
    p([t("Journal: ", { bold: true }), t(JOURNAL, { italics: true })], tight),
    p([t("Manuscript title: ", { bold: true }), t(TITLE)], { spacing: { after: 240 } }),
    p(t("Please tick the statement that applies.", { italics: true }), { spacing: { after: 120 } }),
    option(true, "The authors declare that they have no known competing financial interests or " +
      "personal relationships that could have appeared to influence the work reported in this paper."),
    option(false, "The authors declare the following financial interests/personal relationships " +
      "which may be considered as potential competing interests:"),
    emptyBox,
  ];
  return new Document({
    creator: "", lastModifiedBy: "", title: "Declaration of interests",
    styles, numbering,
    sections: [{ properties: { page: PAGE }, children }],
  });
}

(async () => {
  await save(coverLetter(), "1_Cover_Letter.docx");
  await save(titlePage(), "2_Title_Page_with_Author_Details.docx");
  await save(declaration(), "4_Declaration_of_Competing_Interests.docx");
})();
