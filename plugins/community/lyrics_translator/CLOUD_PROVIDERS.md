# Cloud translation providers

**Lyrics Translator** includes **Azure AI Translator**, **Google Cloud Translation**,
**DeepL API** and **MyMemory** to the Lyrics Editor's **Translations** tab.
Argos is an optional engine in the same plugin; no Argos installation is required
for these cloud services. No additional Python dependencies are needed.

Choose a provider, enter its credentials, select languages, then choose
**Translate missing rows**. Only this action sends lyrics to the selected
service. You can also Ctrl/Shift-select rows and choose **Translate selected rows**
to translate or retry only those rows, preserving all other drafts. Review/edit
the output, then **Apply to lyrics editor**. Applying is
undoable and writes no file; use the editor's normal Save action. Existing
translations, timing, formatting, readings and metadata are retained.

Each provider has its own draft; changing providers does not translate or send
anything automatically. Closing the editor, including Save & Close, warns if
any provider has unapplied translations or running work. Cancel keeps the editor
open; Discard closes and cancels running requests. Apply translations before
saving to include them in your lyrics file.

A failed request never falls back to another provider.
Requests are cancellable, have a 30-second timeout, and are not automatically
retried. A canceled request may already have been processed/billed by the service.
Incomplete or failed runs never apply partial results.

The **Quota** button in the editor's bottom bar opens the selected provider's
quota details. Before translating, the panel estimates the source characters for the next run
after reusing exact duplicate rows. It shows that run as a percentage of the
provider's published reference allowance, including its day/month/year/total
period. DeepL has a separate quota-reference selector because its endpoint alone
does not identify the account's allowance. References are not actual balances.

**Sent from this tab** counts characters in dispatched requests during this
panel's lifetime, including failed/canceled requests that may have reached the
service. Reloading lyrics keeps this count; closing the editor resets it. Other
tabs, prior sessions, other apps, and provider-side adjustments are not included.
The preview does not query your account or send any lyrics. Character estimates
include whitespace and newlines; UTF-8 byte limits remain separate.

## Credentials

- **Azure:** a Translator resource key and, if required by your resource, its
  region. This adapter uses the public global Translator v3 endpoint.
- **Google:** a Google Cloud API key with Cloud Translation Basic v2 enabled.
  Configure API restrictions and billing in your Google Cloud project.
- **DeepL:** an API key. **Automatic** selects the legacy Free endpoint for keys
  ending in `:fx`, otherwise the main API endpoint. You can override the endpoint
  using the account selector to match your account's documentation.
- **MyMemory:** no key required; uses the anonymous public translation endpoint.

Keys are masked in the interface. On Windows, entered keys are saved with
current-user DPAPI encryption in plugin settings and restored on subsequent
launches. The encryption key is managed by Windows, not stored beside the
ciphertext. Each provider uses a separate encrypted record. Clear the API key
field to remove its saved value. Keys are saved when you finish editing, start
translation, or close the panel. Source lyric files never contain credentials.

Saved keys are tied to your Windows account and normally to the same computer;
copying settings to another account or computer may require entering them again.
Applications running as your Windows user can access the same encryption service.
If secure storage is unavailable or saving fails, the UI reports it and keeps
the entered key session-only, without writing a plaintext fallback.

You can also provide
`LRCPLUS_AZURE_API_KEY`, `LRCPLUS_AZURE_REGION`, `LRCPLUS_GOOGLE_API_KEY`, or
`LRCPLUS_DEEPL_API_KEY` in the environment. Environment-provided keys are not
saved unless you edit them. Account endpoints and quotas belong
to the provider; the UI does not promise a fixed free allowance.

## Limits and language coverage

The initial interface offers a common subset: Japanese, English, simplified and
traditional Chinese, Korean, German, French, Spanish, Italian, Portuguese,
Russian, Dutch and Polish. This is not the providers' full language catalog.
Availability of individual routes can depend on your provider/account.

Repeated source text is sent once per run. Azure, Google and DeepL requests
batch up to 40 unique rows with a conservative byte limit. MyMemory accepts at
most 500 UTF-8 bytes per row (not 500 characters); longer rows are rejected
before any request is sent. Other adapters cap individual rows at 5,000
characters. Original row indices preserve alignment even for duplicate timestamps.

## Published quotas checked 2026-11-04

These are provider-published allowances, not an app-enforced billing cap:

| Provider | Published allowance | Source |
| --- | --- | --- |
| Azure F0 | 2 million characters/month | [Pricing](https://azure.microsoft.com/en-us/pricing/details/translator/) |
| Google NMT | First 500,000 characters/month, applied as a credit | [Pricing](https://cloud.google.com/translate/pricing) |
| DeepL API Developer | 1 million characters total | [Billing](https://support.deepl.com/hc/en-us/articles/360020685720-Usage-count-and-billing-in-DeepL-API) |
| DeepL API Free | 500,000 characters/month for that plan | [Billing](https://support.deepl.com/hc/en-us/articles/360020685720-Usage-count-and-billing-in-DeepL-API) |
| MyMemory anonymous | 5,000 characters/day | [Usage limits](https://mymemory.translated.net/doc/usagelimits.php) |

REST references: [Azure](https://learn.microsoft.com/en-us/azure/ai-services/translator/text-translation/reference/v3/translate),
[Google](https://cloud.google.com/translate/docs/reference/rest/v2/translate),
[DeepL](https://developers.deepl.com/api-reference/translate/request-translation),
[MyMemory](https://mymemory.translated.net/doc/spec.php).
