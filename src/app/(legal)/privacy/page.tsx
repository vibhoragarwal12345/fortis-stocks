// TEMPLATE — must be reviewed by a qualified professional before production use.
//
// Reasonable starter Privacy Policy for an invite-only financial-research
// platform that stores user accounts (via Supabase Auth) and displays
// delayed third-party market data. Not legal advice; have counsel review.

export const metadata = { title: "Privacy Policy — Fortis" }

const EFFECTIVE_DATE = "June 11, 2026"

export default function PrivacyPage() {
  return (
    <div className="report-prose">
      <p className="text-eyebrow">Legal</p>
      <h1 className="mt-2 text-h1">Privacy Policy</h1>
      <p className="mt-3 text-small text-muted-foreground">
        Effective {EFFECTIVE_DATE}
      </p>

      <p className="mt-8">
        This Privacy Policy explains how Fortis Stock Intelligence
        (&ldquo;Fortis,&rdquo; &ldquo;we,&rdquo; &ldquo;us&rdquo;), operated by
        The Fortis Agency, collects, uses, and protects personal information
        when you access our stock-research platform (the
        &ldquo;Service&rdquo;). By using the Service you agree to the practices
        described here.
      </p>

      <h2>Information we collect</h2>
      <ul>
        <li>
          <strong>Account information.</strong> Your email address and an
          encrypted password, managed on our behalf by our authentication
          provider. We never store passwords in plaintext.
        </li>
        <li>
          <strong>Usage data.</strong> Product analytics events (for example,
          which pages and reports you open) used to operate and improve the
          Service.
        </li>
        <li>
          <strong>Technical data.</strong> Server logs, IP address, browser
          type, and similar diagnostic information generated when you use the
          Service.
        </li>
      </ul>

      <h2>How we use information</h2>
      <ul>
        <li>To authenticate you and provide the Service.</li>
        <li>To maintain security and prevent abuse.</li>
        <li>To analyze and improve features, performance, and reliability.</li>
        <li>To communicate with you about your account and the Service.</li>
      </ul>

      <h2>Cookies and sessions</h2>
      <p>
        We use strictly necessary cookies to keep you signed in and to secure
        your session. We do not use advertising or third-party tracking
        cookies.
      </p>

      <h2>Service providers</h2>
      <p>
        We share data with vendors who process it solely to deliver the
        Service — including our hosting and authentication provider, our
        transactional email provider, and our market-data providers. They are
        bound to use the data only as instructed. We do not sell personal
        information.
      </p>

      <h2>Market data</h2>
      <p>
        Prices and related figures shown in the Service are provided by
        third-party data vendors and are delayed (typically by at least 15
        minutes). We do not warrant their accuracy or completeness.
      </p>

      <h2>Data retention</h2>
      <p>
        We retain account and usage data for as long as your account is active
        and as needed to provide the Service, comply with legal obligations,
        resolve disputes, and enforce our agreements.
      </p>

      <h2>Security</h2>
      <p>
        We use industry-standard safeguards, including encryption in transit
        and access controls, to protect personal information. No method of
        transmission or storage is completely secure, and we cannot guarantee
        absolute security.
      </p>

      <h2>Your rights</h2>
      <p>
        Depending on your jurisdiction, you may have the right to access,
        correct, export, or delete your personal information, or to object to
        or restrict certain processing. To exercise these rights, contact us
        using the details below.
      </p>

      <h2>International transfers</h2>
      <p>
        Your information may be processed in countries other than your own,
        which may have different data-protection laws. Where required, we use
        appropriate safeguards for such transfers.
      </p>

      <h2>Children</h2>
      <p>
        The Service is intended for investment professionals and is not
        directed to anyone under 18. We do not knowingly collect information
        from children.
      </p>

      <h2>Changes to this policy</h2>
      <p>
        We may update this Privacy Policy from time to time. Material changes
        will be reflected by updating the effective date above and, where
        appropriate, by additional notice.
      </p>

      <h2>Contact</h2>
      <p>
        Questions about this policy or your data can be directed to The Fortis
        Agency at the contact address provided in your onboarding
        communications.
      </p>
    </div>
  )
}
