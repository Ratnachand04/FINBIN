# JFDS submission files

Paper: **Beyond Directional Accuracy: A Verified Walk-Forward Framework for Machine-Learning Cryptocurrency Trading**

Journal: The Journal of Finance and Data Science (KeAi / Elsevier Editorial Manager)

All texts are written in simple, clear English (short sentences, common words), close to IELTS band 6. No grammar mistakes were added on purpose, because poor language can lead to rejection.

## 1. Files to upload now (folder `upload/`)

| Editorial Manager item type | File to upload |
|---|---|
| Cover Letter | `upload/1_Cover_Letter.pdf` |
| Title Page (with Author Details) | `upload/2_Title_Page_with_Author_Details.pdf` |
| Manuscript (without Author Details) | `upload/3_Manuscript_without_Author_Details.pdf` |
| Conflict of Interest | `upload/4_Declaration_of_Competing_Interests.docx` (Word file, as the journal asks) |

The manuscript PDF has no author names, emails, ORCID iDs, affiliations, CRediT roles, funding note or GitHub link, and its PDF metadata is empty. The journal uses double-anonymized review, so it must stay this way.

## 2. Steps in Editorial Manager

1. Open the **Attach Files** page. You already uploaded `beyond directional.pdf` as the Title Page. If that file is the full paper with your names, remove it: click the remove (delete) icon in the **Action** column of that row. The Title Page item should hold only the title page.
2. In **Select Item Type**, choose **Cover Letter**. Click **Browse...** (or drag the file into the box), select `1_Cover_Letter.pdf` and upload it.
3. Choose **Title Page (with Author Details)** and upload `2_Title_Page_with_Author_Details.pdf`.
4. Choose **Manuscript (without Author Details)** and upload `3_Manuscript_without_Author_Details.pdf`.
5. Choose **Conflict of Interest** and upload `4_Declaration_of_Competing_Interests.docx`.
6. Check the list of attached files. The order should be: Cover Letter, Title Page, Manuscript, Conflict of Interest. You can change the order in the **Order** column. Then click **Proceed**.
7. Fill in the next steps (review preferences, additional information, comments, manuscript data). Copy the title, abstract and keywords from section 4 below.
8. Editorial Manager now builds one PDF from your files. Open it with **View Submission** and check it: all pages are there, the figures and tables look right, and the manuscript part shows no names.
9. Click **Approve Submission**. The submission is sent to the journal only after this click. You will get a confirmation email.

Do **not** upload anything from the `editable/` folder now. The journal asks for the LaTeX source only at the revision stage.

## 3. Please check before you click "Approve Submission"

- Both authors agree to submit this paper. The cover letter says so.
- The paper is not under review at another journal. The cover letter says so.
- The co-author email is correct: `saisrinivaspanthangi09@gmail.com`. (In the screenshot the end of the address was cut, so `.com` was assumed.)
- The AI statement in the manuscript and on the title page matches how you really worked.
- If your institute wants a department or school name, add it to the affiliation on the title page and in Editorial Manager.
- The GitHub repository is public and its link is on the title page. The newest research scripts are on branch `claude/brave-edison-zopat8` and on this branch. Please merge them into `main`, so the link shows the same code that the paper describes.

## 4. Text to copy into the Editorial Manager forms

**Title**

Beyond Directional Accuracy: A Verified Walk-Forward Framework for Machine-Learning Cryptocurrency Trading

**Abstract** (244 words, plain text)

Most studies judge machine-learning forecasts of cryptocurrency returns by directional accuracy alone. Accuracy does not show whether a model adds anything to a simple rule, or whether it still earns money after trading costs. We build an evaluation framework that links forecasting, statistical testing and trading economics, avoids look-ahead leakage and tests its own code, and we apply it to daily Binance spot data for Bitcoin, Ether and Dogecoin. The framework uses 30 causal features, walk-forward validation with in-fold scaling and a training–test gap, paired tests against simple baselines, measures based on the size of price moves, and a break-even cost estimated together with turnover. Its tests caught all eight rule violations that we put back into the backtest engine. Over 5,562 out-of-sample forecasts from July 2020 to March 2026, a regularized logistic model is correct 52.95% of the time (95% interval 51.45–54.41%) and beats a coin flip on every asset. A one-day reversal rule scores 52.88%, so much of this skill is shared with it, but the model is better on large moves. It has the higher return-weighted hit rate on all three assets, and its long/short book earns a gross Sharpe ratio of 0.54, against 0.004 for the reversal rule. At 9 basis points per unit of turnover the net Sharpe ratio is 0.11 and the break-even cost is 11.4 basis points, while a long-or-cash version reaches 0.71. The bootstrap intervals are still wide. The full analysis is reproduced by one command.

**Keywords** (one per box)

- Financial machine learning
- Walk-forward validation
- Cryptocurrency
- Forecast evaluation
- Transaction costs
- Backtest verification

**JEL classification:** G17, C53, C58, G14

**Funding:** none (this research did not receive any specific grant).

## 5. About the Conflict of Interest form

The KeAi download link for the official form could not be opened from the environment that made these files. So `4_Declaration_of_Competing_Interests.docx` uses the standard Elsevier/KeAi wording, with the first box ticked: "The authors declare that they have no known competing financial interests or personal relationships that could have appeared to influence the work reported in this paper." It has no names, so it is safe for anonymous review. No signature is needed. If the editorial office asks for its own form, download it from https://www.keaipublishing.com/media/lzgneyd0/declaration-of-competing-interests-form.docx, tick the same first box, save it as .docx and upload it.

## 6. Editable files (folder `editable/`, not for upload now)

| File | Use |
|---|---|
| `1_Cover_Letter.docx` | Word version of the cover letter, if you want to change something |
| `2_Title_Page_with_Author_Details.docx` | Word version of the title page |
| `manuscript_anonymous.tex` | LaTeX source with no author names inside. Upload this as **LaTeX Source File** at the revision stage. |
| `manuscript.tex` | One source for both versions: `\blindtrue` gives the anonymous copy, `\blindfalse` (default) gives the copy with names |
| `Full_paper_with_author_details_DO_NOT_UPLOAD.pdf` | The paper with names, for your own records and the final version |
| `make_word_files.js` | Rebuilds the three Word files: `node make_word_files.js <output-folder>` |
| `make_anonymous.py` | Rebuilds `manuscript_anonymous.tex` from `manuscript.tex` and checks that no author detail is left |

To rebuild the manuscript PDF, compile `manuscript_anonymous.tex` with pdfLaTeX three times (for example in Overleaf). The file needs no other files: the figures are drawn in TikZ and the references are inside the file.

The manuscript text uses the same numbers, tables, equations, references and cross-references as the earlier version. Only the wording was made simpler, and this was checked by a script.
