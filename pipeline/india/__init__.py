"""India (NSE) data-layer ingestors -- counterparts to the US pipeline sources.

  nse_announcements  -> corporate filings/catalysts   (~SEC EDGAR)   [source #2]
  insider_sast       -> SEBI SAST/PIT insider trades   (~Form 4)     [source #3]
  fno_ban            -> daily F&O securities-in-ban     (~crowding)   [source #4]

All free (nsepython), symbol-filtered, and company-verified per record.
Deferred (need a licensed key / not yet wired): fundamentals (#1, no scraping),
macro (#5), transcripts (#6).
"""
