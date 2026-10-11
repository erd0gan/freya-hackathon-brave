// OpenAPI 3.0.3 description of the 7 Freya Bank voice services (from the service contract).
const resultSchema = {
  type: "object",
  description: "Trace and status block. Success: returnCode \"1\". Errors: returnCode == code, messageText == message. messageText is for logs, never read it to the caller.",
  properties: {
    guid: { type: "string", description: "Trace id; give it to the bank when opening a ticket." },
    returnCode: { type: "string", example: "1" },
    messageText: { type: "string", example: "Success" },
    code: { type: "string", example: "1" },
    message: { type: "string", example: "Success" },
    type: { type: "string", nullable: true, enum: ["ERROR", "WARNING"], description: "null on success." },
    details: {
      type: "object",
      properties: {
        guid: { type: "string" },
        rootCause: { type: "string", nullable: true },
        errorOrigin: { type: "string", nullable: true },
      },
    },
  },
};

const callSchema = {
  type: "object",
  description: "Call metadata (logged only, not used for the answer).",
  properties: {
    ani: { type: "string", description: "Caller's number." },
    lang: { type: "string", example: "tr" },
  },
};

const errorSchema = {
  type: "object",
  description: "Error answer: only `result`, no `data`.",
  properties: { result: resultSchema },
  required: ["result"],
};

const str = (description, extra = {}) => ({ type: "string", description, ...extra });
const num = (description) => ({ type: "number", description });
const int = (description) => ({ type: "integer", description });

const debtBlock = (cur) => ({
  type: "object",
  description: `Debt block for ${cur}. The whole block can be missing for a card (missing is not the same as NO_DEBT; do not invent zeros).`,
  properties: {
    statementPaymentStatus: { type: "string", enum: ["HAS_DEBT", "NO_DEBT"] },
    transferAmount: num("Carried over from the previous period."),
    statementDebt: num("Statement debt."),
    minPayment: num("Minimum payment on the statement."),
    remainingDebt: num("What is left of the statement debt after payments since."),
    remainingMinPayment: num("What is left of the minimum payment after payments since."),
    currentPeriodExpense: num("Spending after the statement."),
    totalDebt: num("Includes spending after the statement. Can be negative (credit balance; status NO_DEBT then)."),
    totalDebtPaymentStatus: { type: "string", enum: ["HAS_DEBT", "NO_DEBT"] },
    remainAutomaticPaymentAmount: num("Only meaningful when automaticPaymentFlag is Y."),
  },
});

const ok = (description, data) => ({
  description,
  content: {
    "application/json": {
      schema: {
        type: "object",
        properties: { data: { type: "object", properties: data }, result: resultSchema },
        required: ["data", "result"],
      },
    },
  },
});

const errRes = (description) => ({ description, content: { "application/json": { schema: errorSchema } } });

const errors = (e404) => ({
  400: errRes("INVALID_ARGUMENTS_ERROR: a required field is missing or empty (messageText names the field, e.g. \"card number cannot be empty\")."),
  ...(e404 ? { 404: errRes(e404) } : {}),
  500: errRes("FBE00099: technical error on the bank side (\"Teknik hata: ABORTED: \"). Not a customer problem; do not read messageText to the caller."),
});

const body = (dataProps, required, example) => ({
  required: true,
  content: {
    "application/json": {
      schema: {
        type: "object",
        properties: { data: { type: "object", properties: dataProps, required }, call: callSchema },
        required: ["data"],
      },
      example,
    },
  },
});

const customerNo = { description: "Customer number handed over by the phone menu with the call. String or number both accepted.", oneOf: [{ type: "string" }, { type: "integer" }], example: "90000001" };
const cardNumber = str("Card number EXACTLY as cards.list returned it (masked, e.g. 540000******1234). Never rebuild it from digits the caller says.", { example: "540000******1234" });
const newStatementWeek = { description: "Requested statement week, 1-4 (number or string accepted).", type: "integer", minimum: 1, maximum: 4, example: 2 };
const statementDay = int("The card's current statementDay from statement.week.get.");
const CALL = { ani: "905000000000", lang: "tr" };

const post = (operationId, summary, description, requestBody, responses) => ({
  post: { operationId, summary, description, tags: ["voice"], requestBody, responses },
});

export function buildOpenApi(origin) {
  return {
    openapi: "3.0.3",
    info: {
      title: "Freya Bank voice assistant: statement and debt services (test mock)",
      version: "1.0.0",
      description:
        "Seven POST services (JSON in, JSON out, UTF-8). Dates are strings dd.MM.yyyy; amounts are JSON numbers with a dot decimal, never rounded. The services return raw values only: turning numbers and dates into speech is the assistant's job. Start from cards.list. No authentication is checked in the test environment.",
    },
    servers: [{ url: origin + "/voice/v1" }],
    paths: {
      "/cards.list": post(
        "cards_list",
        "List the customer's active cards",
        "Call first, with the customer number from the call. Returns the cards the assistant may talk about (only active cards, status A). exitState 1_CARD or MULTI_CARD. Two cards can share product name and last 4 digits, so cardNumberLast4 is not unique; keep the full cardNumber and pass it unchanged to the other services. 404 FB_IVR_CHANNEL_102 (WARNING) means the customer has no valid card; this is different from a 500 technical error.",
        body({ customerNo, aiFlow: str("Use case asking; not validated in test.", { enum: ["CardOps"], default: "CardOps" }) }, ["customerNo"], { data: { customerNo: "90000001", aiFlow: "CardOps" }, call: CALL }),
        {
          200: ok("The customer's cards.", {
            cards: {
              type: "array",
              items: {
                type: "object",
                properties: {
                  cardNumber: str("Masked, first 6 + last 4 visible. Pass it unchanged to other services."),
                  cardNumberLast4: str("Last 4 digits (not unique across cards)."),
                  productName: str("Free text from the card system; may be empty."),
                  status: str("A = active."),
                  isMainCard: { type: "boolean", description: "false if it is not the customer's own main card; still listed." },
                },
              },
            },
            validCardsCount: int("Number of cards."),
            exitState: str("1_CARD or MULTI_CARD.", { enum: ["1_CARD", "MULTI_CARD"] }),
          }),
          ...errors("FB_IVR_CHANNEL_102 (WARNING): customer has no valid card."),
        }
      ),
      "/cards.balance": post(
        "cards_balance",
        "Get statement and due dates and debt per currency for a card",
        "Use for questions about the statement date, last payment date, minimum payment or debt. One debt block per currency (debtTL, debtUSD, debtEUR, debtGBP); a block can be missing, which is not the same as NO_DEBT. The cardNumber in the answer is masked differently (first 4 + last 4); do not use it to match against cards.list, keep the number you sent.",
        body({ cardNumber }, ["cardNumber"], { data: { cardNumber: "540000******1234" }, call: CALL }),
        {
          200: ok("Balance and dates.", {
            cardNumber: str("Masked as first 4 + last 4 (5400********1234)."),
            cardNumberLast4: str("Last 4 digits."),
            productName: str("Product name."),
            statementDate: str("Last issued statement, dd.MM.yyyy."),
            lastPayDate: str("Due date of the last statement, dd.MM.yyyy."),
            nextStatementDate: str("Coming statement, dd.MM.yyyy."),
            nextLastPayDate: str("Due date of the coming statement, dd.MM.yyyy."),
            debtTL: debtBlock("TL"),
            debtUSD: debtBlock("USD"),
            debtEUR: debtBlock("EUR"),
            debtGBP: debtBlock("GBP"),
            statementPaymentStatus: { type: "string", enum: ["HAS_DEBT", "NO_DEBT"], description: "For the card overall." },
            totalDebtPaymentStatus: { type: "string", enum: ["HAS_DEBT", "NO_DEBT"], description: "For the card overall." },
            automaticPaymentFlag: str("Y or N.", { enum: ["Y", "N"] }),
          }),
          ...errors("FB_IVR_CHANNEL_120 (WARNING): card not found."),
        }
      ),
      "/statement.week.get": post(
        "statement_week_get",
        "Get the card's current statement week and day",
        "Use before changing the statement week and for questions about which week/day the statement is cut. statementWeek is 1-4, statementDay the day of month (both numbers). relationType is empty when the card has its own statement; M (main) or C (linked) when two cards share one statement, then relatedCardNumber/relatedCardProductName name the other card. Changing the week on either card moves both. There is no field saying whether the customer can still change the week.",
        body({ cardNumber }, ["cardNumber"], { data: { cardNumber: "540000******3201" }, call: CALL }),
        {
          200: ok("Current statement week.", {
            cardNumber: str("Card number."),
            cardNumberLast4: str("Last 4 digits."),
            productName: str("Product name."),
            statementWeek: int("Week of the month, 1-4."),
            statementDay: int("Day of the month."),
            statementDate: str("dd.MM.yyyy."),
            lastPayDate: str("dd.MM.yyyy."),
            nextStatementDate: str("dd.MM.yyyy."),
            nextLastPayDate: str("dd.MM.yyyy."),
            relationType: str("Empty string, M (main card of a shared statement) or C (linked card).", { enum: ["", "M", "C"] }),
            relatedCardNumber: str("The other card of a shared statement, else empty string."),
            relatedCardProductName: str("Product name of the other card, else empty string."),
          }),
          ...errors("FB_IVR_CHANNEL_121 (WARNING): card not found."),
        }
      ),
      "/statement.week.decide": post(
        "statement_week_decide",
        "Preview the dates for a requested statement week (changes nothing)",
        "Preview only: it does NOT change the card. Tells the first statement date and due date the card would get with the requested week. Call it with a week different from the card's current week (asking for the current week returns an error). Tell the caller these dates and get agreement before calling statement_week_change. Note the due date is called statementDueDate here (not lastPayDate).",
        body({ cardNumber, newStatementWeek, statementDay }, ["cardNumber", "newStatementWeek"], { data: { cardNumber: "540000******1234", newStatementWeek: 2, statementDay: 5 }, call: CALL }),
        {
          200: ok("Preview of dates.", {
            statementDate: str("First statement date under the requested week, dd.MM.yyyy."),
            statementDueDate: str("Due date, dd.MM.yyyy."),
            statementDay: int("Day of month."),
            responseCode: int("0 = fine."),
            responseMessage: str("Success."),
          }),
          ...errors(null),
        }
      ),
      "/statement.week.change": post(
        "statement_week_change",
        "Change the card's statement week (side effect)",
        "CHANGES the card. Call only after the caller has agreed to the dates from statement_week_decide, and never retry automatically (side effect). statementDay is required by the request model; send the current day. On a shared statement both cards move. Success = exitState OK and responseCode 0. statementWeek and statementDay come back as STRINGS here; ignore statementDay and tell the caller the dates from statement_week_decide.",
        body({ cardNumber, newStatementWeek, statementDay }, ["cardNumber", "newStatementWeek"], { data: { cardNumber: "540000******1234", newStatementWeek: 2, statementDay: 5 }, call: CALL }),
        {
          200: ok("Result of the change.", {
            responseCode: int("0 = fine."),
            responseMessage: str("For example \"00\"."),
            statementWeek: str("New week as a string."),
            statementDay: str("Day as a string (ignore)."),
            exitState: str("OK on success.", { example: "OK" }),
          }),
          ...errors(null),
        }
      ),
      "/contact.email.get": post(
        "contact_email_get",
        "Get the customer's registered e-mail / phone",
        "Use to find the registered e-mail before sending a statement PDF. The registered e-mail is ibanEmail (ttsEmail is the same address prepared for reading aloud). No registered e-mail means communicationType Phone and/or empty ibanEmail; check the field, not only the type. The iban* prefix is historical (nothing to do with IBANs). The test environment returns the same answer whatever intAddressInfoType is.",
        body({ customerNo, intAddressInfoType: str("E = e-mail, P = phone, B = both. An empty value gives a 400.", { enum: ["E", "P", "B"], default: "E" }) }, ["customerNo"], { data: { customerNo: "90000001", intAddressInfoType: "E" }, call: CALL }),
        {
          200: ok("Registered contact data.", {
            communicationType: str("For example Both or Phone."),
            ibanCountryCode: str("Phone country code."),
            ibanAreaCode: str("Phone area code."),
            ibanPhoneNum: str("Phone number."),
            ibanEmail: str("Registered e-mail; empty string if none."),
            ttsEmail: str("Same address prepared for reading aloud; empty if none."),
            commGramType: str("Communication type flag."),
            exitState: str("Done.", { example: "Done" }),
          }),
          ...errors(null),
        }
      ),
      "/statement.pdf.send": post(
        "statement_pdf_send",
        "E-mail the statement PDF (side effect)",
        "Sends the statement PDF of an issued statement by e-mail. Has a side effect: do not retry automatically. Use only after the caller confirmed the address and send only to the registered e-mail from contact_email_get (the backend does not check the address, so sending elsewhere is a business/compliance decision). statementDate is the statementDate from cards_balance. Success: data.exitState DONE. 404 FB_IVR_CHANNEL_107 means there is no PDF for that statement.",
        body(
          {
            customerNo,
            cardNumber,
            currencyType: str("Currency of the statement.", { enum: ["TL", "USD", "EUR", "GBP"] }),
            statementDate: str("statementDate from cards_balance, dd.MM.yyyy.", { example: "05.10.2026" }),
            emailAddress: str("Destination address; use the registered e-mail from contact_email_get.", { example: "ornek@freyabank.example" }),
          },
          ["customerNo"],
          { data: { customerNo: "90000001", cardNumber: "540000******1234", currencyType: "TL", statementDate: "05.10.2026", emailAddress: "ornek@freyabank.example" }, call: CALL }
        ),
        {
          200: ok("PDF sent.", { exitState: str("DONE on success.", { example: "DONE" }) }),
          ...errors("FB_IVR_CHANNEL_107 (WARNING): no statement document to send."),
        }
      ),
    },
  };
}
