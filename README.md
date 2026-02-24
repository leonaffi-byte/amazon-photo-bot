# 📸 Amazon Photo Bot

A Telegram bot that receives a product photo, identifies it with **GPT-4o Vision**,
and finds the same or similar items on **Amazon** — with special support for
**free delivery to 🇮🇱 Israel**.

---

## Features

| Feature | Detail |
|---|---|
| 🤖 AI Image Recognition | GPT-4o Vision identifies brand, model, features |
| 🛒 Amazon Search | Official PA-API 5.0 — real-time results |
| ✈️ Israel Free Delivery Filter | Toggle on/off — shows only FBA-eligible items |
| 📄 Pagination | Browse results with ◀/▶ buttons |
| 🔄 Live Filter Toggle | Switch filter without re-sending the photo |
| ⭐ Smart Ranking | Results sorted by rating × review count |

---

## Architecture

```
User Photo
    │
    ▼
GPT-4o Vision ──► ProductInfo
    │                (name, brand, features,
    │                 search query, confidence)
    ▼
Amazon PA-API 5.0
    │
    ├── All results (de-duplicated, ranked)
    │
    ▼
Filter (optional)
    │
    ├── IsAmazonFulfilled == true  ──► Free delivery to Israel eligible
    │
    ▼
Telegram Inline Results + Navigation
```

---

## Why GPT-4o for Image Recognition?

- **Reads brand text & model numbers** on packaging without needing training data
- **Context-aware**: understands that a photo of a kitchen tap is plumbing hardware, not art
- **Structured JSON output**: reliable extraction of search-optimised keywords
- **Fast**: ~3–5 s per image vs ~8–12 s for older GPT-4 Turbo Vision
- **Cost-effective**: ~$0.002–0.005 per image analysis

---

## How Free Delivery to Israel Works

Amazon ships to Israel through its **Amazon Global** programme. The rules:

1. **Item must be Fulfilled by Amazon (FBA) or sold by Amazon Retail**
   - These items are in Amazon's international shipping pool
   - Third-party sellers (MFN) almost never offer free international shipping

2. **Cart total must reach $49 USD**
   - This is a threshold for the whole order, not per item

3. **Item must not be export-restricted**
   - Most consumer products are fine

### How we detect eligibility in the API

| PA-API field | What it means | Our use |
|---|---|---|
| `Offers.Listings[0].DeliveryInfo.IsAmazonFulfilled` | Item is in Amazon warehouse | **Primary filter signal** |
| `Offers.Listings[0].MerchantInfo.Name == "Amazon.com"` | Sold by Amazon Retail | Extra confidence indicator |
| `Offers.Listings[0].DeliveryInfo.IsFreeShippingEligible` | Free US shipping | Secondary signal |

**When you enable the Israel filter**, we keep only items where
`IsAmazonFulfilled == true`. These items will have free delivery to Israel
once your cart total exceeds $49.

---

## Setup

### 1. Prerequisites

- Python 3.11+
- A Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- An OpenAI API key with GPT-4o access
- An Amazon Associates account with PA-API 5.0 access

### 2. Amazon PA-API Setup

1. Join [Amazon Associates](https://affiliate-program.amazon.com/)
2. Go to **Tools → Product Advertising API**
3. Request API access (instant for existing Associates)
4. Note your **Access Key**, **Secret Key**, and **Associate Tag**

> ⚠️ PA-API requires at least 3 qualifying sales in 180 days to stay active.
> For testing, you can use [mock mode](#mock-mode).

### 3. Install & Configure

```bash
cd amazon-photo-bot
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your keys
```

`.env` file:
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
OPENAI_API_KEY=sk-...
AMAZON_ACCESS_KEY=AKIA...
AMAZON_SECRET_KEY=...
AMAZON_ASSOCIATE_TAG=yourtag-20
AMAZON_MARKETPLACE=www.amazon.com
RESULTS_PER_PAGE=5
MAX_RESULTS=20
FREE_DELIVERY_THRESHOLD=49
```

### 4. Run

```bash
python main.py
```

---

## Usage

1. Open your bot in Telegram
2. Send `/start` for instructions
3. Send any product photo
4. The bot will:
   - Show what it identified (product name, brand, confidence)
   - Ask if you want to filter by free delivery to Israel
5. Choose your filter preference
6. Browse results with ◀/▶ navigation
7. Tap **View on Amazon** to open any product

---

## Result Ranking

Results are sorted by a **Bayesian-style score**:

```
score = star_rating × log10(review_count + 1)
```

This balances:
- **Quality** (star rating)
- **Confidence in that rating** (more reviews = more reliable)

A 5-star product with 10 reviews ranks below a 4.5-star product with 10,000 reviews.

---

## Project Structure

```
amazon-photo-bot/
├── main.py              # Entry point
├── bot.py               # Telegram handlers, keyboards, session state
├── image_analyzer.py    # GPT-4o Vision integration
├── amazon_search.py     # PA-API 5.0 integration + free delivery detection
├── config.py            # Configuration from .env
├── requirements.txt
├── .env.example
└── README.md
```

---

## Limitations & Notes

- PA-API has a **rate limit of 1 request/second** by default (can be raised)
- Session state is **in-memory** — restarting the bot clears all sessions
- `CustomerReviews` resource in PA-API may require a minimum sales threshold on your Associate account; if reviews are missing, the ranking falls back to Amazon's default relevance order
- The Israel free-delivery filter is a **best-effort heuristic** — always verify on the Amazon product page before purchase

---

## License

MIT
