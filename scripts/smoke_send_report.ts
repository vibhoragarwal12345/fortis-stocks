/**
 * Step 7.3 smoke probe -- exercises sendReport(..., { dryRun: true })
 * against a real Supabase DB without spending Resend credits.
 *
 * Usage:
 *   npx tsx -r dotenv/config scripts/smoke_send_report.ts <report_id> <user_id>
 *
 * Reads SUPABASE_SERVICE_ROLE_KEY + NEXT_PUBLIC_SUPABASE_URL from .env.local
 * (via dotenv -- ensure dotenv_config_path is set if you keep the file
 * outside cwd). Returns ok=true with bytes + truncated flag on success.
 */
import { sendReport } from "@/lib/email"

async function main() {
  const [reportId, userId] = process.argv.slice(2)
  if (!reportId || !userId) {
    console.error("usage: smoke_send_report.ts <report_id> <user_id>")
    process.exit(2)
  }
  const res = await sendReport(reportId, userId, { dryRun: true })
  console.log(JSON.stringify(res, null, 2))
  process.exit(res.ok ? 0 : 1)
}

main().catch(e => {
  console.error(e)
  process.exit(1)
})
