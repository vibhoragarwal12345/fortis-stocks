// TEMPLATE — must be reviewed by a qualified professional before production use.
//
// Reasonable starter Terms & Conditions for an invite-only financial-research
// platform. Not legal advice; have counsel review before production use.

export const metadata = { title: "Terms & Conditions · Christopher Edwards Financial Associates" }

const EFFECTIVE_DATE = "June 11, 2026"

export default function TermsPage() {
  return (
    <div className="report-prose">
      <p className="text-eyebrow">Legal</p>
      <h1 className="mt-2 text-h1">Terms &amp; Conditions</h1>
      <p className="mt-3 text-small text-muted-foreground">
        Effective {EFFECTIVE_DATE}
      </p>

      <p className="mt-8">
        These Terms &amp; Conditions (&ldquo;Terms&rdquo;) govern your access
        to and use of Christopher Edwards Financial Associates (the &ldquo;Service&rdquo;),
        operated by Christopher Edwards Financial Associates. By accessing the Service you agree to
        these Terms. If you do not agree, do not use the Service.
      </p>

      <h2>Eligibility and access</h2>
      <p>
        The Service is offered to investment professionals and other users
        who register for an account. Access is personal to you and may not
        be shared. We may suspend or revoke access at our discretion.
      </p>

      <h2>Your account</h2>
      <p>
        You are responsible for maintaining the confidentiality of your
        credentials and for all activity under your account. Notify us promptly
        of any unauthorized use. We are not liable for losses arising from your
        failure to safeguard your credentials.
      </p>

      <h2>Acceptable use</h2>
      <ul>
        <li>
          Do not redistribute, resell, scrape, or reverse-engineer the Service
          or its data without our written permission.
        </li>
        <li>
          Do not use the Service unlawfully or in a way that disrupts or
          compromises its security or integrity.
        </li>
        <li>
          Do not attempt to access accounts, data, or systems you are not
          authorized to access.
        </li>
      </ul>

      <h2>Not investment advice</h2>
      <p>
        The Service provides research, data, and analytical tools for
        informational purposes only. It does not constitute investment,
        financial, legal, or tax advice, and nothing in it is a recommendation
        or solicitation to buy or sell any security. See our{" "}
        <a href="/disclaimer">Disclaimer</a> for details. You are solely
        responsible for your own investment decisions.
      </p>

      <h2>Market data</h2>
      <p>
        Market data is provided by third parties, is delayed, and may be
        inaccurate, incomplete, or unavailable. It is provided &ldquo;as
        is&rdquo; without warranty of any kind.
      </p>

      <h2>Intellectual property</h2>
      <p>
        The Service, including its content, software, and design, is owned by
        Christopher Edwards Financial Associates or its licensors and is protected by intellectual
        property laws. We grant you a limited, non-exclusive, non-transferable
        right to use the Service in accordance with these Terms.
      </p>

      <h2>Availability and disclaimer of warranties</h2>
      <p>
        The Service is provided &ldquo;as is&rdquo; and &ldquo;as
        available.&rdquo; We do not warrant that it will be uninterrupted,
        error-free, or that any result obtained from it will be accurate or
        reliable. To the maximum extent permitted by law, we disclaim all
        warranties, express or implied.
      </p>

      <h2>Limitation of liability</h2>
      <p>
        To the maximum extent permitted by law, Christopher Edwards Financial Associates will not be
        liable for any indirect, incidental, special, consequential, or
        punitive damages, or for any trading or investment losses, arising out
        of or relating to your use of the Service.
      </p>

      <h2>Termination</h2>
      <p>
        We may suspend or terminate your access at any time, with or without
        notice, for any reason, including breach of these Terms. Provisions
        that by their nature should survive termination will survive.
      </p>

      <h2>Governing law</h2>
      <p>
        These Terms are governed by the laws of the jurisdiction in which Christopher Edwards Financial Associates is established, without regard to conflict-of-laws
        principles.
      </p>

      <h2>Changes to these Terms</h2>
      <p>
        We may update these Terms from time to time. Continued use of the
        Service after changes take effect constitutes acceptance of the revised
        Terms.
      </p>

      <h2>Contact</h2>
      <p>
        Questions about these Terms can be directed to Christopher Edwards Financial Associates at the
        contact address provided in your onboarding communications.
      </p>
    </div>
  )
}
