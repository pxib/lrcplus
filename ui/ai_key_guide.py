"""Local, author-written API setup help; links open only when clicked."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout


GUIDE_PAGES = (
    ("Introduction", """<p>Hey, maiko here. I recommend <a href="https://nano-gpt.com/get-started">NanoGPT</a>
as it allows you to use models without requiring a subscription.</p>
<p>Yes, you can deposit a dollar and use one of the available models they offer
with your balance. Check the current deposit options on their pricing page.</p>
<p>You can still <a href="https://nano-gpt.com/pricing">subscribe for included usage
on eligible models</a> (although there's a caveat: usage and rate limits still
apply. It's not unlimited, and many frontier models still charge your balance.
Okay, I'll stop ruining the fun.)</p>
<p>You can consult the <a href="https://nano-gpt.com/api">API page</a> to create an
API key and connect it here. Sign in if needed, then use <b>Create API key</b>.
Choose a key that can run models, rather than a management-only token.
(I'm not sponsored.)</p>
"""),
    ("NVIDIA NIM", """<p>If you want to try free inference, then I recommend checking out
<a href="https://developer.nvidia.com/nim">NVIDIA NIM</a> and the
<a href="https://build.nvidia.com">NVIDIA API catalog</a> for available free
trial/developer access. Check the current eligibility, quotas, and terms first.</p>
<p>It can be slow at times due to traffic. (Trust me, speaking from experience lmao.)</p>
<p>NVIDIA may ask you to verify a phone number before issuing an API key.
If you're uncomfortable sharing personal details then… yeah, you can choose
another provider yourself. Not saying you NEED to do this!</p>
<p>To get started, sign in to the API catalog, choose a supported text model,
and follow its <b>Get API Key</b> instructions. Use the base URL and model ID
shown in that model's API example.</p>
<p>The <a href="https://docs.api.nvidia.com/nim/docs/api-quickstart">official
quickstart</a> walks through obtaining a key. NVIDIA's
<a href="https://docs.api.nvidia.com/nim/docs/faq">FAQ</a> explains trial access.</p>
"""),
    ("How to set up", """<ol>
<li><p><b>API base URL</b><br>
For NanoGPT, enter <code>https://api.nano-gpt.com/api/v1</code> in
<b>API base URL</b>. Do not append <code>/chat/completions</code> yourself;
LRCPlus adds it. Other providers use their own base URL.</p></li>
<li><p><b>Model ID</b><br>
Copy the exact text-model ID from your provider's catalog or API example.
It often looks like <code>company/model</code>, but this also depends on the
provider. A display name and an API model ID aren't always the same thing.</p></li>
<li><p><b>API key</b><br>
Paste the key you created with your chosen provider into <b>API key</b>.
The providers linked in this guide explain how to create one.</p></li>
<li><p><b>Save and send</b><br>
Click <b>Save</b>, then send a short message to Ayami.
<b>Advanced parameters</b> is optional; leave it alone unless your provider
or model needs extra settings.</p></li>
</ol>
"""),
    ("Info", """<p>Check your provider's usage details and costs before sending your first message~</p>
<p>A website subscription and API access aren't always the same thing.
Check whether your selected model uses included usage, trial access, or a paid balance.</p>
<p>The token counter shows usage reported by your provider. It isn't a price
estimate, and a failed request may still consume tokens.</p>
<p>You can return to this guide whenever you need it. Opening it doesn't
change your settings or send an AI request.</p>
<p>Toodles~ ㅂㅂ ♡</p>
"""),
)


class ApiKeyGuide(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Don't know where to get an API key?")
        self.resize(600, 480)
        self.setMinimumSize(420, 340)
        self.page = 0
        layout = QVBoxLayout(self)
        self.heading = QLabel()
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(self.heading)
        self.content = QTextBrowser()
        self.content.setAccessibleName("API key setup instructions")
        self.content.setOpenExternalLinks(True)
        layout.addWidget(self.content, 1)
        navigation = QHBoxLayout()
        self.back = QPushButton("Previous")
        self.next = QPushButton("Next")
        self.close_button = QPushButton("Close")
        self.progress = QLabel()
        self.progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for button in (self.back, self.next, self.close_button):
            button.setAutoDefault(False)
        self.back.clicked.connect(lambda: self.set_page(self.page - 1))
        self.next.clicked.connect(self.advance)
        self.close_button.clicked.connect(self.reject)
        navigation.addWidget(self.back)
        navigation.addWidget(self.progress, 1)
        navigation.addWidget(self.next)
        navigation.addWidget(self.close_button)
        layout.addLayout(navigation)
        self.set_page(0)

    def set_page(self, page):
        if not 0 <= page < len(GUIDE_PAGES):
            return
        self.page = page
        title, body = GUIDE_PAGES[page]
        self.heading.setText(title)
        link_color = self.palette().link().color().name()
        self.content.document().setDefaultStyleSheet(
            f"p {{ margin-bottom: 12px; }} a {{ color: {link_color}; }}")
        self.content.setHtml(body)
        self.content.verticalScrollBar().setValue(0)
        self.progress.setText(f"Step {page + 1} of {len(GUIDE_PAGES)}")
        self.back.setEnabled(page > 0)
        self.next.setText("Done" if page == len(GUIDE_PAGES) - 1 else "Next")

    def advance(self):
        if self.page == len(GUIDE_PAGES) - 1:
            self.accept()
        else:
            self.set_page(self.page + 1)


def show_api_key_guide(parent):
    dialog = ApiKeyGuide(parent)
    try:
        dialog.exec()
    finally:
        dialog.deleteLater()
