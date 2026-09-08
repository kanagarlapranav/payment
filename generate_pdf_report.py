import os
from datetime import datetime
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_header_footer(num_pages)
            super().showPage()
        super().save()

    def draw_header_footer(self, total_pages):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#718096"))
        
        # Header (pages > 1)
        if self._pageNumber > 1:
            self.drawString(40, 810, "Technical Audit Report — Payment Tracker Telegram Bot")
            self.drawRightString(555, 810, "Confidential / Development Review")
            self.setStrokeColor(colors.HexColor("#E2E8F0"))
            self.setLineWidth(0.5)
            self.line(40, 804, 555, 804)

        # Footer (all pages)
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(40, 45, 555, 45)
        self.drawString(40, 32, "Target: kanagarlapranav/payment | Render & Telegram Bot Architecture")
        page_str = f"Page {self._pageNumber} of {total_pages}"
        self.drawRightString(555, 32, page_str)
        self.restoreState()

def generate_report(output_filename="Codebase_Audit_Report.pdf"):
    doc = SimpleDocTemplate(
        output_filename,
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=50,
        bottomMargin=55
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#1A202C")
    )

    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10.5,
        leading=15,
        textColor=colors.HexColor("#4A5568")
    )

    h1_style = ParagraphStyle(
        'Heading1_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#1A365D"),
        spaceBefore=12,
        spaceAfter=6
    )

    h2_style = ParagraphStyle(
        'Heading2_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#2B6CB0"),
        spaceBefore=8,
        spaceAfter=4
    )

    body_style = ParagraphStyle(
        'Body_Custom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#2D3748")
    )

    body_bold = ParagraphStyle(
        'Body_Bold',
        parent=body_style,
        fontName='Helvetica-Bold'
    )

    code_style = ParagraphStyle(
        'Code_Style',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=7.5,
        leading=10.5,
        textColor=colors.HexColor("#9B2C2C")
    )

    table_cell = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10.5,
        textColor=colors.HexColor("#2D3748")
    )

    table_cell_bold = ParagraphStyle(
        'TableCellBold',
        parent=table_cell,
        fontName='Helvetica-Bold'
    )

    story = []

    # Title Block
    story.append(Paragraph("Codebase Audit & Architecture Review", title_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph("<b>Subject:</b> Evaluation of Technical Queries & Architectural Feedback for Personal Payment Tracker Bot", subtitle_style))
    story.append(Paragraph("<b>Target Repository:</b> github.com/kanagarlapranav/payment &nbsp;|&nbsp; <b>Evaluation Date:</b> September 2026", subtitle_style))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#3182CE"), spaceBefore=2, spaceAfter=10))

    # Executive Verdict Box
    exec_content = [
        [
            Paragraph(
                "<b>Executive Verdict: Is an Upgrade Required?</b><br/><br/>"
                "<b>Yes, an upgrade is strongly recommended</b>, primarily due to an active <b>credential exposure</b> and <b>authorization vulnerabilities</b>. "
                "Your developer friend's review was exceptionally sharp and grounded in genuine production risks. While the bot currently functions well for basic personal transactions, "
                "the real Telegram Bot Token was committed directly into git history, button-based callbacks lack authorization checks, and specific balance-altering commands bypass the cloud backup engine.<br/><br/>"
                "This report provides an itemized technical evaluation of each query, code-verified findings, and an actionable upgrade roadmap.",
                body_style
            )
        ]
    ]
    exec_table = Table(exec_content, colWidths=[515])
    exec_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#EBF8FF")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#BEE3F8")),
        ('LINELEFT', (0,0), (0,-1), 4, colors.HexColor("#3182CE")),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(exec_table)
    story.append(Spacer(1, 10))

    # 1. Summary Matrix Table
    story.append(Paragraph("1. Summary Review Matrix", h1_style))
    
    headers = [
        Paragraph("<b>Query / Item</b>", table_cell_bold),
        Paragraph("<b>Friend's Claim</b>", table_cell_bold),
        Paragraph("<b>Code Verification</b>", table_cell_bold),
        Paragraph("<b>Severity</b>", table_cell_bold),
        Paragraph("<b>Action Required?</b>", table_cell_bold),
        Paragraph("<b>Effort</b>", table_cell_bold)
    ]
    
    rows = [
        headers,
        [
            Paragraph("<b>1. Leaked Bot Token</b><br/><code>check_messages.py</code><br/><code>setup_env.py</code>", table_cell),
            Paragraph("Hardcoded in plain source & git history", table_cell),
            Paragraph("<b>Confirmed</b><br/>Committed in <code>affae641</code>", table_cell),
            Paragraph("<font color='#9B2C2C'><b>CRITICAL</b></font>", table_cell),
            Paragraph("<b>Immediate</b> (Revoke & Rotate)", table_cell),
            Paragraph("5 mins", table_cell)
        ],
        [
            Paragraph("<b>2. Access Control</b><br/>Callbacks & Groups", table_cell),
            Paragraph("Check if commands/actions restricted", table_cell),
            Paragraph("<b>Gap Found</b><br/>Callbacks unverified", table_cell),
            Paragraph("<font color='#C05621'><b>HIGH</b></font>", table_cell),
            Paragraph("<b>Yes</b> (Add auth to callbacks)", table_cell),
            Paragraph("10 mins", table_cell)
        ],
        [
            Paragraph("<b>3. Deployment Conflict</b><br/>Render vs Vercel/Netlify", table_cell),
            Paragraph("Incompatible architecture files present", table_cell),
            Paragraph("<b>Confirmed</b><br/>Polling vs Serverless", table_cell),
            Paragraph("<font color='#B7791F'><b>MEDIUM</b></font>", table_cell),
            Paragraph("<b>Yes</b> (Purge dead configs)", table_cell),
            Paragraph("5 mins", table_cell)
        ],
        [
            Paragraph("<b>4. Tesseract OCR</b><br/>Linux OS dependency", table_cell),
            Paragraph("Fails on Render without Dockerfile", table_cell),
            Paragraph("<b>Shielded</b><br/>RapidOCR is primary", table_cell),
            Paragraph("<font color='#4A5568'><b>LOW</b></font>", table_cell),
            Paragraph("Optional (Clean setup exe)", table_cell),
            Paragraph("5 mins", table_cell)
        ],
        [
            Paragraph("<b>5. DB Persistence</b><br/>Ephemeral disk on Render", table_cell),
            Paragraph("Risk of data loss on container restarts", table_cell),
            Paragraph("<b>Gap Found</b><br/><code>/setbalance</code> unbacked", table_cell),
            Paragraph("<font color='#B7791F'><b>MEDIUM</b></font>", table_cell),
            Paragraph("<b>Yes</b> (Trigger backup on write)", table_cell),
            Paragraph("5 mins", table_cell)
        ],
        [
            Paragraph("<b>6. Keep-Alive Ping</b><br/>Internal vs External", table_cell),
            Paragraph("Self-ping cannot revive frozen process", table_cell),
            Paragraph("<b>Confirmed</b><br/>Burns 744h free quota", table_cell),
            Paragraph("<font color='#4A5568'><b>LOW</b></font>", table_cell),
            Paragraph("Recommended (External cron)", table_cell),
            Paragraph("10 mins", table_cell)
        ],
        [
            Paragraph("<b>7. Utility Scripts</b><br/>Duplicated setup code", table_cell),
            Paragraph("Redundant near-identical files", table_cell),
            Paragraph("<b>Confirmed</b><br/>Duplicate polling logic", table_cell),
            Paragraph("<font color='#718096'><b>LOW</b></font>", table_cell),
            Paragraph("Optional (Consolidate)", table_cell),
            Paragraph("5 mins", table_cell)
        ]
    ]

    matrix_table = Table(rows, colWidths=[110, 105, 95, 60, 100, 45])
    matrix_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#EDF2F7")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFC")]),
    ]))
    story.append(matrix_table)
    story.append(Spacer(1, 10))

    # Page Break for Deep Dive
    story.append(PageBreak())

    # 2. Detailed Technical Findings
    story.append(Paragraph("2. Technical Verification & Solutions", h1_style))

    # Point 1
    p1_box = [
        [
            Paragraph("<b>Point 1: Hardcoded Token in Source Files & Git History (Critical)</b>", h2_style)
        ],
        [
            Paragraph(
                "<b>Observation:</b> <code>check_messages.py</code> (line 6) and <code>setup_env.py</code> (line 6) hardcoded the live production Telegram Bot Token: "
                "<code>8863268724:AAFcDfpdgTXas2E6OnNIQj9mRIwRzQ8WV94</code>.<br/>"
                "<b>Git Verification:</b> Confirmed. Both files were included in initial commit <code>affae64155db5ab9eddcce8afe31bf6379790193</code> and pushed to remote <code>origin/main</code>. "
                "Because Git retains full commit history, removing the lines in a future commit does not protect the token — any user or crawler viewing public commit history can extract it.<br/>"
                "<b>Impact:</b> Full takeover of bot identity, access to payment screenshots, and potential tampering with financial data.<br/>"
                "<b>Solution:</b><br/>"
                "1. Revoke the token immediately via Telegram <b>@BotFather</b> (<code>/mybots</code> &rarr; select bot &rarr; API Token &rarr; Revoke).<br/>"
                "2. Place the new token into Render environment variables and local <code>.env</code>.<br/>"
                "3. Refactor <code>check_messages.py</code> and <code>setup_env.py</code> to load from <code>os.getenv('TELEGRAM_BOT_TOKEN')</code>.",
                body_style
            )
        ]
    ]
    t_p1 = Table(p1_box, colWidths=[515])
    t_p1.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#FFF5F5")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#FEB2B2")),
        ('LINELEFT', (0,0), (0,-1), 4, colors.HexColor("#E53E3E")),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(t_p1)
    story.append(Spacer(1, 8))

    # Point 2
    p2_box = [
        [
            Paragraph("<b>Point 2: Command & Callback Authorization Gaps (High)</b>", h2_style)
        ],
        [
            Paragraph(
                "<b>Observation:</b> Your friend advised checking whether command access is gated to authorized users.<br/>"
                "<b>Code Verification:</b><br/>"
                "&bull; <b>Commands are protected:</b> Text commands (<code>/balance</code>, <code>/history</code>, <code>/delete</code>, etc.) correctly call <code>is_authorized(update)</code>.<br/>"
                "&bull; <b>Vulnerability in Callbacks:</b> In <code>bot/handlers.py:171</code>, <code>handle_callback_query()</code> <b>does not check authorization</b>. Any user who can see or tap inline buttons can execute <code>confirm_tx</code>, <code>cancel_tx</code>, or <code>delete_confirm</code>.<br/>"
                "&bull; <b>Group ID Broadness:</b> In <code>bot/commands.py:23</code>, <code>is_authorized</code> returns <code>True</code> if <code>chat_id == TELEGRAM_GROUP_ID</code>. If the bot is in a group with multiple users, any member can execute commands.<br/>"
                "<b>Solution:</b> Insert <code>if not await is_authorized(update): return</code> at line 174 of <code>bot/handlers.py</code>, and restrict destructive commands (<code>/setbalance</code>, <code>/delete</code>) strictly to <code>TELEGRAM_USER_ID</code>.",
                body_style
            )
        ]
    ]
    t_p2 = Table(p2_box, colWidths=[515])
    t_p2.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#FFFAF0")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#FBD38D")),
        ('LINELEFT', (0,0), (0,-1), 4, colors.HexColor("#DD6B20")),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(t_p2)
    story.append(Spacer(1, 8))

    # Point 3
    p3_box = [
        [
            Paragraph("<b>Point 3: Conflicting Deployment Architectures (Render vs Vercel / Netlify)</b>", h2_style)
        ],
        [
            Paragraph(
                "<b>Observation:</b> The repository contains conflicting deployment files: <code>render.yaml</code> + <code>Procfile</code> (daemon long-polling) vs <code>vercel.json</code> + <code>netlify.toml</code> + <code>api/index.py</code> (serverless webhook).<br/>"
                "<b>Code Verification:</b> Confirmed. Long-polling and serverless are architecturally incompatible for this application. In serverless functions, SQLite files do not persist across invocations, background threads are terminated on response return, and free execution timeouts (10s) trigger OCR failures.<br/>"
                "<b>Danger:</b> Running <code>set_webhook.py</code> permanently disables Render's long-polling until explicitly deleted from Telegram.<br/>"
                "<b>Solution:</b> Standardize on Render. Remove <code>vercel.json</code>, <code>netlify.toml</code>, <code>set_webhook.py</code>, and <code>api/index.py</code>.",
                body_style
            )
        ]
    ]
    t_p3 = Table(p3_box, colWidths=[515])
    t_p3.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F7FAFC")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#E2E8F0")),
        ('LINELEFT', (0,0), (0,-1), 4, colors.HexColor("#4A5568")),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(t_p3)
    story.append(Spacer(1, 8))

    # Point 4
    p4_box = [
        [
            Paragraph("<b>Point 4: Tesseract OCR vs RapidOCR on Render Cloud</b>", h2_style)
        ],
        [
            Paragraph(
                "<b>Observation:</b> Friend noted that <code>pytesseract</code> requires OS-level binary <code>apt-get install tesseract-ocr</code>, which Render's native Python runtime lacks.<br/>"
                "<b>Code Verification:</b> Your code in <code>ocr/engine.py:32-43</code> already uses <b>RapidOCR (ONNX Runtime)</b> as the primary OCR engine. RapidOCR runs purely within Python without external OS dependencies. Tesseract is only a fallback and is skipped safely via <code>shutil.which('tesseract')</code>.<br/>"
                "<b>Cleanup:</b> <code>tesseract_setup.exe</code> (50 MB installer) is sitting in the root directory, and <code>pytesseract</code> is in <code>requirements.txt</code>. Both can be cleaned up to streamline repo size and deployment times.",
                body_style
            )
        ]
    ]
    t_p4 = Table(p4_box, colWidths=[515])
    t_p4.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F0FFF4")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#C6F6D5")),
        ('LINELEFT', (0,0), (0,-1), 4, colors.HexColor("#38A169")),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(t_p4)
    story.append(Spacer(1, 8))

    # Page Break for Points 5-7 and Roadmap
    story.append(PageBreak())

    # Point 5
    p5_box = [
        [
            Paragraph("<b>Point 5: SQLite Database Persistence & Backup Reliability</b>", h2_style)
        ],
        [
            Paragraph(
                "<b>Observation:</b> Render free-tier disk is ephemeral and resets on every redeploy/restart. Backups must run on every write.<br/>"
                "<b>Code Verification:</b> You implemented a resilient backup mechanism via <code>services/backup_service.py</code> that backs up to Telegram and restores on startup.<br/>"
                "<b>Identified Vulnerability:</b> Backups trigger after image transactions, button confirms, and deletes, but <b>NOT</b> after <code>/setbalance &lt;amount&gt;</code> in <code>bot/commands.py:530</code>. If Render restarts after a balance adjustment, the new balance is reverted to the prior backup.<br/>"
                "<b>Solution:</b> Add <code>asyncio.create_task(backup_to_telegram(context.bot))</code> inside <code>setbalance_command()</code>.",
                body_style
            )
        ]
    ]
    t_p5 = Table(p5_box, colWidths=[515])
    t_p5.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#FFFAF0")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#FBD38D")),
        ('LINELEFT', (0,0), (0,-1), 4, colors.HexColor("#DD6B20")),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(t_p5)
    story.append(Spacer(1, 8))

    # Point 6 & 7
    p6_box = [
        [
            Paragraph("<b>Point 6 & 7: Keep-Alive Pinging & Script Deduplication</b>", h2_style)
        ],
        [
            Paragraph(
                "<b>Keep-Alive & Free Quota:</b> <code>app.py:91-103</code> runs an internal daemon pinging <code>RENDER_EXTERNAL_URL</code> every 10 minutes. "
                "Render Free Web Services allocate <b>750 free instance hours/month</b>. Running 24/7/365 consumes 744 hours (99.2% of account allowance). "
                "If the Python process crashes, the internal thread dies with it. An external uptime monitor (e.g. <i>cron-job.org</i> or <i>UptimeRobot</i>) is more resilient.<br/><br/>"
                "<b>Script Deduplication:</b> <code>check_messages.py</code> (times out after 15 attempts) and <code>setup_env.py</code> (infinite loop) duplicate 90% of the same code. They can be merged into a single clean <code>get_chat_ids.py</code> utility.",
                body_style
            )
        ]
    ]
    t_p6 = Table(p6_box, colWidths=[515])
    t_p6.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F7FAFC")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#E2E8F0")),
        ('LINELEFT', (0,0), (0,-1), 4, colors.HexColor("#4A5568")),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(t_p6)
    story.append(Spacer(1, 12))

    # 3. Prioritized Upgrade Roadmap Table
    story.append(Paragraph("3. Recommended Upgrade Action Plan", h1_style))

    roadmap_headers = [
        Paragraph("<b>Phase</b>", table_cell_bold),
        Paragraph("<b>Concrete Action Items</b>", table_cell_bold),
        Paragraph("<b>Timing & Priority</b>", table_cell_bold),
        Paragraph("<b>Risk If Postponed</b>", table_cell_bold)
    ]

    roadmap_rows = [
        roadmap_headers,
        [
            Paragraph("<b>Phase 1<br/>Security Hardening</b>", table_cell),
            Paragraph(
                "1. Revoke leaked Telegram token via @BotFather.<br/>"
                "2. Update token in local <code>.env</code> & Render dashboard.<br/>"
                "3. Remove token string from <code>check_messages.py</code> & <code>setup_env.py</code>.<br/>"
                "4. Add <code>is_authorized()</code> to <code>handle_callback_query()</code> in <code>bot/handlers.py</code>.",
                table_cell
            ),
            Paragraph("<b>Immediate</b><br/>(Within 24-48 hrs)", table_cell),
            Paragraph("<font color='#9B2C2C'><b>Bot takeover & data leakage</b></font>", table_cell)
        ],
        [
            Paragraph("<b>Phase 2<br/>Data Integrity</b>", table_cell),
            Paragraph(
                "1. Add Telegram backup trigger to <code>/setbalance</code>.<br/>"
                "2. Remove dead serverless configs (<code>vercel.json</code>, <code>netlify.toml</code>, <code>api/</code>).<br/>"
                "3. Remove 50MB <code>tesseract_setup.exe</code> from repository.",
                table_cell
            ),
            Paragraph("<b>Next Update</b><br/>(When convenient)", table_cell),
            Paragraph("<font color='#C05621'>Loss of balance state on Render restart</font>", table_cell)
        ],
        [
            Paragraph("<b>Phase 3<br/>Maintenance</b>", table_cell),
            Paragraph(
                "1. Connect free external uptime ping (cron-job.org / UptimeRobot).<br/>"
                "2. Consolidate setup scripts into a single <code>get_chat_ids.py</code>.",
                table_cell
            ),
            Paragraph("<b>Optional</b><br/>(Maintenance cycle)", table_cell),
            Paragraph("Exhaustion of free monthly Render hours", table_cell)
        ]
    ]

    roadmap_table = Table(roadmap_rows, colWidths=[80, 235, 95, 105])
    roadmap_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#EDF2F7")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFC")]),
    ]))
    story.append(roadmap_table)
    story.append(Spacer(1, 14))

    # Concluding Note
    summary_p = Paragraph(
        "<b>Summary & Next Steps:</b> Since you confirmed the bot is currently working well for your needs, you can keep running it without immediate disruption. "
        "However, because the bot token is committed to Git history, prioritizing <b>Phase 1 (Token Rotation & Callback Auth)</b> will ensure your private financial tracker remains completely secure.",
        body_style
    )
    story.append(summary_p)

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Report generated successfully: {output_filename}")

if __name__ == '__main__':
    generate_report()
