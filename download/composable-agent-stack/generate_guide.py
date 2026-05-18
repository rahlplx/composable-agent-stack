#!/usr/bin/env python3
"""
Generate Composable AI Agent Stack Architecture Guide PDF
Uses ReportLab for structured document production
"""

import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch, cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, Image, KeepTogether
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
import hashlib

# ── Font Registration ──
pdfmetrics.registerFont(TTFont('DejaVuSerif', '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf'))
pdfmetrics.registerFont(TTFont('DejaVuSerif-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf'))
pdfmetrics.registerFont(TTFont('DejaVuSans', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
pdfmetrics.registerFont(TTFont('DejaVuMono', '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'))
registerFontFamily('DejaVuSerif', normal='DejaVuSerif', bold='DejaVuSerif-Bold')
registerFontFamily('DejaVuSans', normal='DejaVuSans', bold='DejaVuSans-Bold')

# ── Colors ──
ACCENT = colors.HexColor('#2563EB')
TEXT_PRIMARY = colors.HexColor('#1F2937')
TEXT_MUTED = colors.HexColor('#6B7280')
BG_SURFACE = colors.HexColor('#F3F4F6')
BG_PAGE = colors.HexColor('#FFFFFF')
TABLE_HEADER = colors.HexColor('#1E3A5F')
TABLE_ROW_ODD = colors.HexColor('#F8FAFC')
GREEN = colors.HexColor('#059669')
AMBER = colors.HexColor('#D97706')

OUTPUT = '/home/z/my-project/download/composable-agent-stack/Composable-Agent-Stack-Guide.pdf'

# ── Styles ──
styles = getSampleStyleSheet()

title_style = ParagraphStyle('DocTitle', fontName='DejaVuSerif', fontSize=32, leading=40,
    alignment=TA_CENTER, textColor=ACCENT, spaceAfter=12)

subtitle_style = ParagraphStyle('DocSubtitle', fontName='DejaVuSans', fontSize=14, leading=20,
    alignment=TA_CENTER, textColor=TEXT_MUTED, spaceAfter=30)

h1_style = ParagraphStyle('H1', fontName='DejaVuSerif', fontSize=20, leading=26,
    textColor=ACCENT, spaceBefore=24, spaceAfter=12)

h2_style = ParagraphStyle('H2', fontName='DejaVuSerif', fontSize=15, leading=20,
    textColor=TEXT_PRIMARY, spaceBefore=18, spaceAfter=8)

h3_style = ParagraphStyle('H3', fontName='DejaVuSerif', fontSize=12, leading=16,
    textColor=ACCENT, spaceBefore=12, spaceAfter=6)

body_style = ParagraphStyle('Body', fontName='DejaVuSerif', fontSize=10.5, leading=17,
    alignment=TA_JUSTIFY, textColor=TEXT_PRIMARY, spaceAfter=8)

body_left_style = ParagraphStyle('BodyLeft', fontName='DejaVuSerif', fontSize=10.5, leading=17,
    alignment=TA_LEFT, textColor=TEXT_PRIMARY, spaceAfter=4)

code_style = ParagraphStyle('Code', fontName='DejaVuMono', fontSize=9, leading=14,
    textColor=colors.HexColor('#1E293B'), backColor=colors.HexColor('#F1F5F9'),
    leftIndent=12, rightIndent=12, spaceBefore=6, spaceAfter=6)

bullet_style = ParagraphStyle('Bullet', fontName='DejaVuSerif', fontSize=10.5, leading=17,
    alignment=TA_LEFT, textColor=TEXT_PRIMARY, leftIndent=24, bulletIndent=12,
    spaceAfter=4)

header_cell_style = ParagraphStyle('HeaderCell', fontName='DejaVuSerif', fontSize=10,
    textColor=colors.white, alignment=TA_CENTER)

cell_style = ParagraphStyle('Cell', fontName='DejaVuSerif', fontSize=9.5,
    textColor=TEXT_PRIMARY, alignment=TA_CENTER)

cell_left_style = ParagraphStyle('CellLeft', fontName='DejaVuSerif', fontSize=9.5,
    textColor=TEXT_PRIMARY, alignment=TA_LEFT)

caption_style = ParagraphStyle('Caption', fontName='DejaVuSerif', fontSize=9,
    textColor=TEXT_MUTED, alignment=TA_CENTER, spaceBefore=4, spaceAfter=12)


# ── TOC Template ──
class TocDocTemplate(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        if hasattr(flowable, 'bookmark_name'):
            level = getattr(flowable, 'bookmark_level', 0)
            text = getattr(flowable, 'bookmark_text', '')
            key = getattr(flowable, 'bookmark_key', '')
            self.notify('TOCEntry', (level, text, self.page, key))


def add_heading(text, style, level=0):
    key = 'h_%s' % hashlib.md5(text.encode()).hexdigest()[:8]
    p = Paragraph('<a name="%s"/>%s' % (key, '<b>%s</b>' % text), style)
    p.bookmark_name = text
    p.bookmark_level = level
    p.bookmark_text = text
    p.bookmark_key = key
    return p


def make_table(headers, rows, col_widths=None):
    """Create a styled table with header and data rows."""
    data = [[Paragraph('<b>%s</b>' % h, header_cell_style) for h in headers]]
    for row in rows:
        data.append([Paragraph(str(c), cell_left_style if len(str(c)) > 30 else cell_style) for c in row])

    available = A4[0] - 2 * inch
    if col_widths is None:
        n = len(headers)
        col_widths = [available / n] * n

    t = Table(data, colWidths=col_widths, hAlign='CENTER')
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), TABLE_HEADER),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D1D5DB')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style_cmds.append(('BACKGROUND', (0, i), (-1, i), TABLE_ROW_ODD))
        else:
            style_cmds.append(('BACKGROUND', (0, i), (-1, i), colors.white))
    t.setStyle(TableStyle(style_cmds))
    return t


def build_pdf():
    doc = TocDocTemplate(OUTPUT, pagesize=A4,
        leftMargin=1*inch, rightMargin=1*inch,
        topMargin=0.8*inch, bottomMargin=0.8*inch)

    story = []

    # ── Cover ──
    story.append(Spacer(1, 120))
    story.append(Paragraph('<b>Composable AI Agent Stack</b>', title_style))
    story.append(Spacer(1, 12))
    story.append(Paragraph('Architecture Guide', ParagraphStyle('CoverSub',
        fontName='DejaVuSerif', fontSize=18, leading=24,
        alignment=TA_CENTER, textColor=TEXT_PRIMARY)))
    story.append(Spacer(1, 24))
    story.append(Paragraph('Agent-S + Browser Use + OpenHands unified through LiteLLM', subtitle_style))
    story.append(Spacer(1, 20))

    cover_features = [
        ['Any API Key', 'Local or Cloud', 'Zero Vendor Lock-in', 'Self-Hosted'],
    ]
    avail = A4[0] - 2*inch
    ft = Table(cover_features, colWidths=[avail/4]*4, hAlign='CENTER')
    ft.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#EFF6FF')),
        ('TEXTCOLOR', (0,0), (-1,-1), ACCENT),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,-1), 'DejaVuSans'),
        ('FONTSIZE', (0,0), (-1,-1), 11),
        ('FONTWEIGHT', (0,0), (-1,-1), 'BOLD'),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#BFDBFE')),
        ('INNERGRID', (0,0), (-1,-1), 1, colors.HexColor('#BFDBFE')),
    ]))
    story.append(ft)

    story.append(Spacer(1, 60))
    story.append(Paragraph('Version 1.0 | May 2026', ParagraphStyle('CoverMeta',
        fontName='DejaVuMono', fontSize=10, alignment=TA_CENTER, textColor=TEXT_MUTED)))

    story.append(PageBreak())

    # ── TOC ──
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle('TOC1', fontName='DejaVuSerif', fontSize=13, leftIndent=20, spaceBefore=8, spaceAfter=4),
        ParagraphStyle('TOC2', fontName='DejaVuSerif', fontSize=11, leftIndent=40, spaceBefore=4, spaceAfter=2),
    ]
    story.append(Paragraph('<b>Table of Contents</b>', h1_style))
    story.append(Spacer(1, 12))
    story.append(toc)
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════
    # SECTION 1: Architecture Overview
    # ══════════════════════════════════════════════════════════════
    story.append(add_heading('1. Architecture Overview', h1_style, 0))

    story.append(Paragraph(
        'The Composable AI Agent Stack is a vendor-agnostic architecture that combines three best-of-breed '
        'open-source agent platforms with a unified LLM gateway. Instead of relying on a single monolithic '
        'platform like Open Computer Use (Coasty), which has severe vendor lock-in (Amazon Bedrock only, '
        'missing core executor, single contributor), this stack assembles independently swappable components '
        'that each excel at a specific type of computer automation. The result is a system that works with '
        'any API key, runs locally or in the cloud, and has zero vendor lock-in at every layer.',
        body_style))

    story.append(add_heading('1.1 The Four-Layer Architecture', h2_style, 1))

    story.append(Paragraph(
        'The stack is organized into four distinct layers, each independently configurable and replaceable. '
        'This separation of concerns means that swapping your LLM provider does not require changing any agent '
        'tool configuration, and replacing one agent tool does not affect the others. The layers communicate '
        'through standard OpenAI-compatible HTTP APIs, making the entire stack transparent and debuggable.',
        body_style))

    layer_table = make_table(
        ['Layer', 'Component', 'Role', 'Replaceable With'],
        [
            ['Layer 2', 'Agent-S', 'Full desktop control (mouse, keyboard, screen)', 'CUA Driver, UFO'],
            ['Layer 2', 'Browser Use', 'Browser automation (Playwright + AI)', 'Stagehand, Skyvern'],
            ['Layer 2', 'OpenHands', 'Software development agent', 'Aider, SWE-agent'],
            ['Layer 1', 'LiteLLM', 'Unified LLM gateway (OpenAI-compatible)', 'OpenRouter, vLLM'],
            ['Layer 0', 'Ollama', 'Local model runner (optional)', 'vLLM, LM Studio, LocalAI'],
        ],
        col_widths=[55, 70, 190, 130]
    )
    story.append(layer_table)
    story.append(Paragraph('Table 1: Architecture layers and replaceable alternatives', caption_style))

    story.append(add_heading('1.2 How the Pieces Connect', h2_style, 1))

    story.append(Paragraph(
        'All three agent tools connect to LiteLLM through its OpenAI-compatible API endpoint. LiteLLM acts as '
        'a transparent proxy: it receives requests in OpenAI format, routes them to the configured provider '
        '(OpenAI, Anthropic, Google, Ollama, etc.), and returns responses in the same format. The agent tools '
        'never see the actual provider and never need direct API keys. This architecture provides several '
        'critical advantages: centralized key management, automatic failover between providers, unified cost '
        'tracking, load balancing across multiple deployments, and the ability to swap providers without '
        'touching any agent configuration.',
        body_style))

    connect_table = make_table(
        ['Agent Tool', 'Connection Method', 'Config Parameter'],
        [
            ['Agent-S', 'OpenAI SDK (raw)', 'base_url=http://litellm:4000/v1'],
            ['Browser Use', 'ChatOpenAI (LangChain-style)', 'base_url=http://litellm:4000/v1'],
            ['OpenHands', 'LiteLLM SDK (native)', 'base_url=http://litellm:4000'],
        ],
        col_widths=[80, 160, 205]
    )
    story.append(connect_table)
    story.append(Paragraph('Table 2: How each agent tool connects to LiteLLM', caption_style))

    # ══════════════════════════════════════════════════════════════
    # SECTION 2: LiteLLM Configuration
    # ══════════════════════════════════════════════════════════════
    story.append(add_heading('2. LiteLLM: The LLM Gateway', h1_style, 0))

    story.append(Paragraph(
        'LiteLLM is the keystone of the entire architecture. It is an open-source AI gateway that provides '
        'a single OpenAI-compatible API to 140+ LLM providers and 2,600+ models. By placing LiteLLM between '
        'your agent tools and your LLM providers, you gain centralized key management, automatic failover, '
        'load balancing, cost tracking, and virtual key management with per-team budgets and rate limits. '
        'The proxy runs as a Docker container with a PostgreSQL database for persistent configuration, and '
        'includes a web dashboard for monitoring spend, managing keys, and viewing analytics.',
        body_style))

    story.append(add_heading('2.1 Supported Providers', h2_style, 1))

    provider_table = make_table(
        ['Category', 'Providers', 'Key Required?'],
        [
            ['Cloud APIs', 'OpenAI, Anthropic, Google Gemini, DeepSeek', 'Yes (per-provider)'],
            ['Cloud Platforms', 'AWS Bedrock, Azure OpenAI, Google Vertex AI', 'Yes (cloud credentials)'],
            ['Routers', 'OpenRouter, Together AI, Fireworks AI, Groq', 'Yes (router key)'],
            ['Self-Hosted', 'Ollama, vLLM, LM Studio, LocalAI', 'No (runs locally)'],
            ['OpenAI-Compatible', 'Any endpoint speaking OpenAI API format', 'Depends on endpoint'],
        ],
        col_widths=[90, 230, 125]
    )
    story.append(provider_table)
    story.append(Paragraph('Table 3: LiteLLM provider categories', caption_style))

    story.append(add_heading('2.2 Key Features', h2_style, 1))

    features = [
        '<b>Load Balancing:</b> Multiple deployments with the same model name are automatically load balanced. Routing strategies include least-busy, latency-based, and cost-based routing.',
        '<b>Automatic Failover:</b> If a primary model fails, LiteLLM automatically tries configured fallback models in order. For example, if GPT-4o is unavailable, it can fall back to Claude Sonnet, then Gemini Flash.',
        '<b>Virtual Keys:</b> Create API keys scoped to specific models, budgets, and rate limits. Issue separate keys per team or per agent tool with spend caps and expiration dates.',
        '<b>Cost Tracking:</b> Automatic spend tracking per key, per user, per team, and per model. Built-in cost map for all known models with support for custom pricing.',
        '<b>Self-Hosted:</b> Full control over your data and configuration. No information leaves your infrastructure. Deploy via Docker Compose with PostgreSQL for persistence.',
    ]
    for f in features:
        story.append(Paragraph(f, bullet_style, bulletText='\xe2\x80\xa2'))

    story.append(add_heading('2.3 Configuration Example', h2_style, 1))

    story.append(Paragraph(
        'The following YAML configuration demonstrates a production-ready LiteLLM setup with multiple '
        'cloud providers, automatic failover, and load balancing. All API keys are read from environment '
        'variables, never hardcoded in the configuration file.',
        body_style))

    config_lines = [
        'model_list:',
        '  - model_name: gpt-4o',
        '    litellm_params:',
        '      model: openai/gpt-4o',
        '      api_key: os.environ/OPENAI_API_KEY',
        '  - model_name: claude-sonnet',
        '    litellm_params:',
        '      model: anthropic/claude-sonnet-4-20250514',
        '      api_key: os.environ/ANTHROPIC_API_KEY',
        '  - model_name: llama3-local',
        '    litellm_params:',
        '      model: ollama/llama3',
        '      api_base: http://ollama:11434',
        'router_settings:',
        '  routing_strategy: least-busy',
        '  fallbacks:',
        '    - model_name: gpt-4o',
        '      fallbacks: [claude-sonnet, llama3-local]',
        'general_settings:',
        '  master_key: os.environ/LITELLM_MASTER_KEY',
    ]
    for line in config_lines:
        story.append(Paragraph(line, code_style))

    # ══════════════════════════════════════════════════════════════
    # SECTION 3: Agent Tool Integration
    # ══════════════════════════════════════════════════════════════
    story.append(add_heading('3. Agent Tool Integration', h1_style, 0))

    # ── Agent-S ──
    story.append(add_heading('3.1 Agent-S: Full Desktop Control', h2_style, 1))

    story.append(Paragraph(
        'Agent-S is the only open-source agent that provides true full-desktop control, making it the closest '
        'alternative to Coasty\'s computer use capabilities. It can move your mouse, type on your keyboard, '
        'take screenshots, and interact with any native desktop application including Excel, Photoshop, VS Code, '
        'and legacy enterprise software. Agent-S was the first agent to beat humans on the OSWorld benchmark '
        '(72.6% accuracy), demonstrating its effectiveness at general computer interaction tasks. The framework '
        'supports multiple LLM providers through its engine abstraction layer, and connecting it to LiteLLM '
        'requires zero code changes, just configuration of the base_url parameter.',
        body_style))

    story.append(Paragraph('<b>LiteLLM Integration (zero code changes):</b>', body_left_style))
    integration_code = [
        '# CLI approach',
        'python -m gui_agents.s3.cli_app \\',
        '  --provider openai \\',
        '  --model gpt-4o \\',
        '  --model_url http://litellm:4000/v1 \\',
        '  --model_api_key sk-litellm-key',
        '',
        '# Programmatic approach',
        'engine_params = {',
        '    "engine_type": "openai",',
        '    "model": "gpt-4o",',
        '    "base_url": "http://litellm:4000/v1",',
        '    "api_key": "sk-litellm-key",',
        '}',
    ]
    for line in integration_code:
        story.append(Paragraph(line, code_style))

    # ── Browser Use ──
    story.append(add_heading('3.2 Browser Use: Browser Automation', h2_style, 1))

    story.append(Paragraph(
        'Browser Use is the most popular open-source browser automation agent with over 94,000 GitHub stars. '
        'It combines AI reasoning with Playwright browser automation to perform complex web tasks including '
        'form filling, web scraping, multi-tab navigation, and SaaS workflow automation. The framework '
        'supports 15+ LLM providers out of the box and has a built-in ChatLiteLLM wrapper class for direct '
        'LiteLLM integration. Browser Use also offers a desktop application and cloud deployment options, '
        'making it suitable for both individual developers and enterprise deployments. Its MIT license ensures '
        'full commercial use without restrictions.',
        body_style))

    story.append(Paragraph('<b>LiteLLM Integration (three methods, zero code changes):</b>', body_left_style))
    bu_code = [
        '# Method 1: ChatOpenAI with base_url (simplest)',
        'from browser_use.llm.openai.chat import ChatOpenAI',
        'llm = ChatOpenAI(model="gpt-4o",',
        '    base_url="http://litellm:4000/v1",',
        '    api_key="sk-litellm-key")',
        '',
        '# Method 2: ChatLiteLLM (full routing)',
        'from browser_use.llm.litellm.chat import ChatLiteLLM',
        'llm = ChatLiteLLM(model="openai/gpt-4o",',
        '    api_base="http://litellm:4000",',
        '    api_key="sk-litellm-key")',
    ]
    for line in bu_code:
        story.append(Paragraph(line, code_style))

    # ── OpenHands ──
    story.append(add_heading('3.3 OpenHands: Software Development Agent', h2_style, 1))

    story.append(Paragraph(
        'OpenHands is a full-featured AI software development agent with over 74,000 GitHub stars. It can write '
        'code, run tests, debug issues, create pull requests, and deploy applications, all within sandboxed '
        'execution environments. Unlike the other two tools, OpenHands uses LiteLLM as its native LLM SDK, '
        'meaning the integration is first-class and fully supported out of the box. OpenHands provides a web '
        'UI for interactive development, a CLI for automation, and an SDK for programmatic integration. Its '
        'MIT license and massive contributor community make it the most mature and well-supported option for '
        'software development automation.',
        body_style))

    story.append(Paragraph('<b>LiteLLM Integration (first-class, zero code changes):</b>', body_left_style))
    oh_code = [
        '# Environment variables (used in docker-compose.yml)',
        'LLM_MODEL=gpt-4o',
        'LLM_API_KEY=sk-litellm-key',
        'LLM_BASE_URL=http://litellm:4000',
        '',
        '# Or via config.toml:',
        '[llm]',
        'model = "gpt-4o"',
        'api_key = "sk-litellm-key"',
        'base_url = "http://litellm:4000"',
        '',
        '# Or via SDK:',
        'from openhands.sdk.llm import LLM',
        'llm = LLM(model="openai/gpt-4o",',
        '    api_key=SecretStr("sk-litellm-key"),',
        '    base_url="http://litellm:4000")',
    ]
    for line in oh_code:
        story.append(Paragraph(line, code_style))

    # ══════════════════════════════════════════════════════════════
    # SECTION 4: Deployment
    # ══════════════════════════════════════════════════════════════
    story.append(add_heading('4. Deployment Guide', h1_style, 0))

    story.append(add_heading('4.1 Quick Start (5 Minutes)', h2_style, 1))

    steps = [
        '<b>Step 1:</b> Clone the stack configuration: <font name="DejaVuSans" size="9">git clone &lt;stack-repo&gt; &amp;&amp; cd composable-agent-stack</font>',
        '<b>Step 2:</b> Configure API keys: <font name="DejaVuSans" size="9">cp litellm/.env.example litellm/.env</font> then edit with your provider keys',
        '<b>Step 3:</b> Start LiteLLM and Browser Use: <font name="DejaVuSans" size="9">docker compose up -d litellm litellm-db browser-use</font>',
        '<b>Step 4:</b> Verify LiteLLM is running: <font name="DejaVuSans" size="9">curl http://localhost:4000/health/liveliness</font>',
        '<b>Step 5:</b> Start OpenHands (optional): <font name="DejaVuSans" size="9">docker compose up -d openhands</font>',
    ]
    for s in steps:
        story.append(Paragraph(s, bullet_style, bulletText='\xe2\x80\xa2'))

    story.append(add_heading('4.2 Local-Only Mode (No Cloud API Keys)', h2_style, 1))

    story.append(Paragraph(
        'For fully offline operation, start Ollama alongside the stack. LiteLLM will route requests to local '
        'models when no cloud providers are configured or when cloud providers are unavailable. This mode is '
        'ideal for air-gapped environments, sensitive data processing, or cost-free experimentation. Simply '
        'start the stack with the local-llm profile: <font name="DejaVuSans" size="9">docker compose --profile local-llm up -d</font>. '
        'Then pull a model: <font name="DejaVuSans" size="9">docker exec ollama ollama pull llama3</font>. '
        'LiteLLM will automatically detect and route to Ollama based on the model name configured in config.yaml.',
        body_style))

    story.append(add_heading('4.3 Cloud Deployment', h2_style, 1))

    story.append(Paragraph(
        'The same Docker Compose configuration works in cloud environments (AWS ECS, Google Cloud Run, Azure '
        'Container Instances) with minimal modifications. The key changes are: (1) replace localhost URLs with '
        'internal service DNS names, (2) add TLS termination via a load balancer or reverse proxy, (3) configure '
        'managed database services instead of the Docker PostgreSQL container for production reliability, and '
        '(4) set up proper authentication and network security groups. The architecture is cloud-agnostic by '
        'design; no cloud-specific services are required.',
        body_style))

    # ══════════════════════════════════════════════════════════════
    # SECTION 5: Comparison with Open Computer Use
    # ══════════════════════════════════════════════════════════════
    story.append(add_heading('5. Comparison: Composable Stack vs Open Computer Use', h1_style, 0))

    story.append(Paragraph(
        'The following table provides a direct comparison between the Composable AI Agent Stack and Open '
        'Computer Use (Coasty). The composable stack addresses every critical weakness identified in our '
        'deep audit of the Coasty platform, including the missing core executor, Bedrock-only LLM provider, '
        'single contributor risk, and lack of CI/CD infrastructure.',
        body_style))

    compare_table = make_table(
        ['Criteria', 'Open Computer Use (Coasty)', 'Composable Stack'],
        [
            ['Full source available?', 'No (core executor missing)', 'Yes (all three tools fully open)'],
            ['LLM provider support', 'Amazon Bedrock only', '140+ providers via LiteLLM'],
            ['Local LLM (Ollama)', 'Disabled in production', 'Fully supported'],
            ['Custom API endpoint', 'Not supported', 'Any OpenAI-compatible endpoint'],
            ['Desktop control', 'Yes (VM-based)', 'Yes (Agent-S, local or VM)'],
            ['Browser automation', 'Yes (in-VM Selenium)', 'Yes (Browser Use + Playwright)'],
            ['Software development', 'No dedicated agent', 'Yes (OpenHands sandbox)'],
            ['License', 'Apache-2.0 (incomplete)', 'MIT + Apache-2.0 (complete)'],
            ['Commercial use', 'Yes (but incomplete)', 'Yes (fully functional)'],
            ['Self-hostable', 'Partial (needs Python backend)', 'Fully self-hosted'],
            ['Contributors', '1 person', '100+ across three projects'],
            ['CI/CD', 'None', 'Active CI in all three projects'],
            ['Community size', '~600 stars', '180,000+ combined stars'],
            ['Vendor lock-in risk', 'Severe (Bedrock, Supabase, Stripe)', 'None (every layer swappable)'],
        ],
        col_widths=[120, 155, 170]
    )
    story.append(compare_table)
    story.append(Paragraph('Table 4: Detailed comparison between Open Computer Use and the Composable Stack', caption_style))

    # ══════════════════════════════════════════════════════════════
    # SECTION 6: Use Cases
    # ══════════════════════════════════════════════════════════════
    story.append(add_heading('6. Use Case Matrix', h1_style, 0))

    story.append(Paragraph(
        'Each agent tool in the composable stack excels at different types of automation tasks. The following '
        'matrix helps you select the right tool for each use case. For workflows that span multiple domains '
        '(desktop, browser, and code), the tools can be composed together, with each handling its area of '
        'specialty while sharing the same LLM backend through LiteLLM.',
        body_style))

    usecase_table = make_table(
        ['Use Case', 'Best Tool', 'Why'],
        [
            ['Automate desktop apps (Excel, QuickBooks)', 'Agent-S', 'Only tool with full desktop control'],
            ['Web scraping and form filling', 'Browser Use', '94K+ stars, Playwright-based, most mature'],
            ['Code generation and bug fixing', 'OpenHands', 'Sandboxed execution, self-correcting'],
            ['RPA replacement', 'Agent-S + Browser Use', 'Desktop apps + web forms coverage'],
            ['QA testing (web)', 'Browser Use', 'Cross-browser, automated screenshots'],
            ['QA testing (desktop)', 'Agent-S', 'Native app interaction and validation'],
            ['CI/CD automation', 'OpenHands', 'Git operations, test running, deployment'],
            ['Lead generation', 'Browser Use', 'LinkedIn/Sales Navigator automation'],
            ['Legacy system integration', 'Agent-S', 'Bridge old desktop apps to modern APIs'],
            ['E-commerce management', 'Browser Use', 'Multi-platform product listing sync'],
            ['Security auditing', 'OpenHands', 'Code scanning, vulnerability detection'],
            ['Invoice processing', 'Agent-S + Browser Use', 'Desktop PDF + web portal submission'],
        ],
        col_widths=[145, 105, 195]
    )
    story.append(usecase_table)
    story.append(Paragraph('Table 5: Use case to tool mapping', caption_style))

    # ══════════════════════════════════════════════════════════════
    # SECTION 7: Why This Is Future-Proof
    # ══════════════════════════════════════════════════════════════
    story.append(add_heading('7. Why This Stack Is Future-Proof', h1_style, 0))

    reasons = [
        ('<b>No vendor lock-in:</b> Each layer is independently swappable. Replace LiteLLM with OpenRouter. '
         'Replace Browser Use with Stagehand. Replace OpenAI with Anthropic. Zero rewrite required because '
         'every component communicates through standard OpenAI-compatible APIs.'),
        ('<b>Any API key, any endpoint:</b> Cloud keys (OpenAI, Anthropic, Google) or local models (Ollama, vLLM) '
         'or custom endpoints (LiteLLM proxy, corporate gateways). LiteLLM normalizes everything behind one '
         'endpoint. Your agent tools never see the difference.'),
        ('<b>Local or cloud hosted:</b> Run everything on your laptop with Ollama for zero-cost operation, or '
         'deploy to AWS/GCP/Azure with managed APIs for production scale. Same configuration, different .env file. '
         'The Docker Compose setup works identically in both environments.'),
        ('<b>Community-backed:</b> The three agent tools have a combined 180,000+ GitHub stars and hundreds of '
         'active contributors. If one project goes stale, alternatives exist at every layer. No single point '
         'of failure like the single-contributor Coasty project.'),
        ('<b>Standards-based:</b> All communication uses the OpenAI API format, which has become the de facto '
         'standard for LLM interactions. Any new tool that speaks this format can slot into the stack '
         'immediately without modification.'),
        ('<b>Composable by design:</b> Need only browser automation? Run Browser Use alone. Need desktop + code? '
         'Combine Agent-S and OpenHands. The stack scales up and down based on your actual needs, without '
         'forcing you to install or maintain components you do not use.'),
    ]
    for r in reasons:
        story.append(Paragraph(r, bullet_style, bulletText='\xe2\x80\xa2'))

    # ── Build ──
    doc.multiBuild(story)
    print(f'PDF generated: {OUTPUT}')
    print(f'Size: {os.path.getsize(OUTPUT) / 1024:.0f} KB')


if __name__ == '__main__':
    build_pdf()
