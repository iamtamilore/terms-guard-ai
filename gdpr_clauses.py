# GDPR-annotated reference clause corpus for Terms Guard AI.
# Each clause maps to specific GDPR articles from the EU General Data Protection
# Regulation (2018), as interpreted by the Irish Data Protection Commission (DPC).
# Legal basis: EXAM_REFERENCE_SHEET.md — H9DGAE NCI Dublin 2024/2026.

ETHICAL_REFERENCE_CLAUSES = [
    # Art.17 — Right to Erasure ("Right to be Forgotten")
    {"text": "You have the right to delete your personal data at any time.", "gdpr": "Art.17 Right to Erasure"},
    {"text": "You can request permanent deletion of your account and all associated data.", "gdpr": "Art.17 Right to Erasure"},
    {"text": "Users can delete their data without penalty or fee.", "gdpr": "Art.17 Right to Erasure"},
    {"text": "We will securely erase your data within 30 days of request.", "gdpr": "Art.17 Right to Erasure"},

    # Art.5(1)(b) — Purpose Limitation (no selling/sharing beyond stated purpose)
    {"text": "We will never sell your personal information to third parties.", "gdpr": "Art.5(1)(b) Purpose Limitation"},
    {"text": "Your data will never be shared with third-party advertisers.", "gdpr": "Art.5(1)(b) Purpose Limitation"},
    {"text": "We do not sell data to any external companies or partners.", "gdpr": "Art.5(1)(b) Purpose Limitation"},
    {"text": "Third parties have no access to your personal information.", "gdpr": "Art.5(1)(b) Purpose Limitation"},

    # Art.7 — Conditions for Consent
    {"text": "You can opt-out of data collection with a single click.", "gdpr": "Art.7 Conditions for Consent"},
    {"text": "Explicit consent is required before any data sharing.", "gdpr": "Art.7 Conditions for Consent"},
    {"text": "You can withdraw your consent at any time without consequence.", "gdpr": "Art.7(3) Right to Withdraw Consent"},
    {"text": "Opt-in (not opt-out) is required for marketing communications.", "gdpr": "Art.7 Conditions for Consent"},
    {"text": "Users must affirmatively consent before tracking begins.", "gdpr": "Art.7 Conditions for Consent + ePrivacy Directive"},

    # Art.5(1)(f) + Art.32 — Integrity, Confidentiality & Security of Processing
    {"text": "Your data is encrypted and stored securely.", "gdpr": "Art.5(1)(f) Integrity & Confidentiality + Art.32"},
    {"text": "We use industry-standard encryption for all data.", "gdpr": "Art.32 Security of Processing"},
    {"text": "Data is protected with end-to-end encryption.", "gdpr": "Art.32 Security of Processing"},
    {"text": "Security audits are conducted regularly by third parties.", "gdpr": "Art.32 Security of Processing"},

    # Art.5(1)(a) + Art.12–14 — Transparency & Right to be Informed
    {"text": "We provide transparent information about data usage.", "gdpr": "Art.5(1)(a) Transparency + Art.13 Right to be Informed"},
    {"text": "We clearly disclose all data collection practices.", "gdpr": "Art.13 Right to be Informed"},
    {"text": "You receive detailed reports of how your data is used.", "gdpr": "Art.5(1)(a) Transparency"},
    {"text": "We explain our data practices in plain language.", "gdpr": "Art.12 Transparent Communication"},

    # Art.20 — Right to Data Portability
    {"text": "You retain full ownership of your content.", "gdpr": "Art.20 Right to Data Portability"},
    {"text": "You can export all your personal data at any time.", "gdpr": "Art.20 Right to Data Portability"},
    {"text": "Your content remains yours unless explicitly transferred.", "gdpr": "Art.20 Right to Data Portability"},
    {"text": "Data portability is available in standard formats.", "gdpr": "Art.20 Right to Data Portability"},

    # Art.5(1)(b) + ePrivacy Directive — No Cross-site Tracking
    {"text": "We do not track your activity across other websites.", "gdpr": "Art.5(1)(b) Purpose Limitation + ePrivacy Directive"},
    {"text": "We respect Do Not Track signals from your browser.", "gdpr": "Art.5(1)(b) Purpose Limitation + ePrivacy Directive"},
    {"text": "We do not use cookies to monitor browsing on other sites.", "gdpr": "ePrivacy Directive + Art.5(1)(b)"},
    {"text": "Cross-site tracking is disabled by default.", "gdpr": "Art.25 Privacy by Design & Default"},

    # Art.15 — Right of Access
    {"text": "You can request a copy of all data we hold about you.", "gdpr": "Art.15 Right of Access"},
    {"text": "Data access requests are fulfilled within 30 days.", "gdpr": "Art.12 + Art.15 Right of Access"},
    {"text": "You receive a complete copy of your data in machine-readable format.", "gdpr": "Art.15(3) Right of Access"},
    {"text": "All information about you is made available upon request.", "gdpr": "Art.15 Right of Access"},

    # Art.5(1)(c) + Art.25 — Data Minimisation & Privacy by Design
    {"text": "We only collect data necessary for service provision.", "gdpr": "Art.5(1)(c) Data Minimisation"},
    {"text": "Data collection is limited to specified purposes.", "gdpr": "Art.5(1)(c) Data Minimisation"},
    {"text": "We minimize personal information collection by default.", "gdpr": "Art.5(1)(c) Data Minimisation + Art.25"},
    {"text": "Unnecessary or excessive data collection is prohibited.", "gdpr": "Art.5(1)(c) Data Minimisation"},

    # Art.7 — Granular Consent Management
    {"text": "Your consent is required before any data sharing.", "gdpr": "Art.7 Conditions for Consent"},
    {"text": "Consent decisions can be modified or revoked anytime.", "gdpr": "Art.7(3) Right to Withdraw Consent"},
    {"text": "Granular consent controls allow selective data sharing.", "gdpr": "Art.7 Conditions for Consent"},
    {"text": "Default settings prioritize user privacy.", "gdpr": "Art.25 Privacy by Design & Default"},

    # Art.5(1)(e) — Storage Limitation
    {"text": "We delete data regularly and don't retain it longer than needed.", "gdpr": "Art.5(1)(e) Storage Limitation"},
    {"text": "Data retention periods are clearly specified.", "gdpr": "Art.5(1)(e) Storage Limitation + Art.13"},
    {"text": "Automatic deletion occurs after the stated retention period.", "gdpr": "Art.5(1)(e) Storage Limitation"},
    {"text": "Historical data is purged according to retention schedule.", "gdpr": "Art.5(1)(e) Storage Limitation"},

    # Art.5(2) Accountability + Art.28 Processor Agreements + Art.8 Child Consent
    {"text": "GDPR compliance is guaranteed for all users.", "gdpr": "Art.5(2) Accountability"},
    {"text": "We follow strict data processing agreements with partners.", "gdpr": "Art.28 Processor Agreements"},
    {"text": "Children's data receives extra protective measures.", "gdpr": "Art.8 Child Consent + Art.9 Special Categories"},
    {"text": "Changes to privacy policy require explicit user consent.", "gdpr": "Art.7 Conditions for Consent + Art.13"},
]

NON_ETHICAL_REFERENCE_CLAUSES = [
    # Art.5(1)(b) violation — Purpose Limitation / Data Sales
    {"text": "We may sell your data to third-party partners.", "gdpr": "Art.5(1)(b) Purpose Limitation violation"},
    {"text": "Your personal information may be sold to advertisers.", "gdpr": "Art.5(1)(b) Purpose Limitation violation"},
    {"text": "We monetize user data by selling to brokers.", "gdpr": "Art.5(1)(b) Purpose Limitation violation"},
    {"text": "Your data is a commodity we trade with other companies.", "gdpr": "Art.5(1)(b) Purpose Limitation violation"},

    # ePrivacy Directive + Art.5(1)(b) violation — Unlawful Tracking
    {"text": "We track your browsing activity across all websites.", "gdpr": "ePrivacy Directive violation + Art.5(1)(b)"},
    {"text": "We use cookies to monitor all your online behavior.", "gdpr": "ePrivacy Directive violation + Art.7 Consent"},
    {"text": "Continuous tracking is enabled by default.", "gdpr": "Art.25 Privacy by Design violation + ePrivacy"},
    {"text": "Your location is tracked constantly for any purpose.", "gdpr": "Art.5(1)(b) Purpose Limitation violation"},

    # Art.82 — Right to Compensation (waiver is unenforceable under GDPR)
    {"text": "You waive your right to legal action against us.", "gdpr": "Art.82 Right to Compensation — unenforceable waiver"},
    {"text": "Users cannot sue us for any data violation.", "gdpr": "Art.82 Right to Compensation violation"},
    {"text": "We are exempt from liability for privacy breaches.", "gdpr": "Art.82 Controller Liability violation"},
    {"text": "You forfeit the right to join class action lawsuits.", "gdpr": "Art.82 Right to Compensation violation"},

    # Art.82 + Art.32 — Liability Disclaimer / Security
    {"text": "We are not liable for any data breaches.", "gdpr": "Art.82 Controller Liability + Art.32 Security"},
    {"text": "Unauthorized access to your data is not our responsibility.", "gdpr": "Art.82 Controller Liability violation"},
    {"text": "We cannot be held responsible for third-party leaks.", "gdpr": "Art.28 Processor Accountability violation"},
    {"text": "Data loss is not covered by any warranty or insurance.", "gdpr": "Art.82 Right to Compensation violation"},

    # Art.44–49 — Unlawful International Transfers
    {"text": "Your data may be transferred to any country worldwide.", "gdpr": "Art.44 International Transfers violation"},
    {"text": "Data can be moved to countries with weak privacy laws.", "gdpr": "Art.44 International Transfers violation"},
    {"text": "We may relocate your information without notice.", "gdpr": "Art.44 + Art.13 Right to be Informed violation"},
    {"text": "International data transfers happen without restriction.", "gdpr": "Art.44–49 International Transfers violation"},

    # Art.13/14 violation — Unilateral Changes / No Notice
    {"text": "We reserve the right to change terms without notice.", "gdpr": "Art.13 Right to be Informed violation"},
    {"text": "Terms of service can be altered at any time unilaterally.", "gdpr": "Art.13 + Art.7 Consent violation"},
    {"text": "Privacy policy modifications take effect immediately.", "gdpr": "Art.13 Right to be Informed violation"},
    {"text": "Users have no advance notice of policy changes.", "gdpr": "Art.13 Right to be Informed violation"},

    # Art.7 violation — Implicit / Passive Consent
    {"text": "We collect data from your device without explicit consent.", "gdpr": "Art.7 Conditions for Consent violation"},
    {"text": "Continued use implies consent to all data practices.", "gdpr": "Art.7 Conditions for Consent violation — passive consent"},
    {"text": "Silence equals agreement to our data collection.", "gdpr": "Art.7 Conditions for Consent violation"},
    {"text": "Data collection begins upon account creation without confirmation.", "gdpr": "Art.7 Conditions for Consent + Art.25 violation"},

    # Art.5(1)(b) + Art.28 — Unauthorised Third-party Sharing
    {"text": "Your information may be shared with affiliated companies.", "gdpr": "Art.5(1)(b) Purpose Limitation + Art.28"},
    {"text": "We sell data to thousands of partner organizations.", "gdpr": "Art.5(1)(b) Purpose Limitation violation"},
    {"text": "Sharing occurs without your knowledge or ability to opt out.", "gdpr": "Art.7 Consent + Art.5(1)(a) Transparency violation"},
    {"text": "Partners can use your data for their own marketing.", "gdpr": "Art.5(1)(b) Purpose Limitation violation"},

    # Art.5(1)(e) + Art.17 violation — Indefinite Retention
    {"text": "We retain your data indefinitely even after account deletion.", "gdpr": "Art.5(1)(e) Storage Limitation + Art.17 violation"},
    {"text": "Deleted data is kept in backup systems forever.", "gdpr": "Art.5(1)(e) Storage Limitation violation"},
    {"text": "We maintain historical records of all your activity indefinitely.", "gdpr": "Art.5(1)(e) Storage Limitation violation"},
    {"text": "Data purges never occur regardless of your request.", "gdpr": "Art.17 Right to Erasure violation"},

    # Art.32 + Art.5(1)(f) violation — Inadequate Security
    {"text": "We provide no encryption or security measures.", "gdpr": "Art.32 Security of Processing violation"},
    {"text": "Your data is stored in plain text with minimal protection.", "gdpr": "Art.32 + Art.5(1)(f) violation"},
    {"text": "We do not conduct security audits or testing.", "gdpr": "Art.32 Security of Processing violation"},
    {"text": "Breaches are our customers' responsibility to detect.", "gdpr": "Art.33 Breach Notification violation"},

    # Art.15, Art.3, Art.7(4) — Access Denial / Jurisdiction Evasion / Coerced Consent
    {"text": "You cannot access or download your data.", "gdpr": "Art.15 Right of Access violation"},
    {"text": "GDPR and privacy laws do not apply to our service.", "gdpr": "Art.3 Territorial Scope — unlawful claim"},
    {"text": "We discriminate based on consent to data sharing.", "gdpr": "Art.7(4) Conditional consent prohibition"},
    {"text": "Privacy choices are not reversible once made.", "gdpr": "Art.7(3) Right to Withdraw Consent violation"},
]

# Flat text lists used for embedding (backward-compatible with existing code)
ETHICAL_TEXTS = [c["text"] for c in ETHICAL_REFERENCE_CLAUSES]
NON_ETHICAL_TEXTS = [c["text"] for c in NON_ETHICAL_REFERENCE_CLAUSES]
