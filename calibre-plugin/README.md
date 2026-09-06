# Skrivist Calibre Plugin

Send books from Calibre directly to your [Skriv.ist](https://skriv.ist) cloud library with one click.

## Installation

### Step 1 — Download the plugin

Go to the [Releases page](https://github.com/gandtr/skrivist.tools/releases/latest) and download `skrivist-calibre-plugin-vX.X.X.zip`.

> Do **not** unzip it — Calibre installs directly from the zip file.

### Step 2 — Install in Calibre

1. Open Calibre
2. Go to **Preferences** (toolbar or `Ctrl+P`)
3. Click **Plugins**
4. Click **Load plugin from file** (bottom of the window)
5. Select the downloaded `.zip` file
6. Click **Yes** on the security warning
7. Restart Calibre when prompted

### Step 3 — Generate an API key

1. Sign in at [https://app.skriv.ist](https://app.skriv.ist)
2. Open **Settings** (gear icon)
3. Expand the **Calibre integration** section
4. Click **Generate API Key**
5. Copy the key (it starts with `sk_...`) — you won't see it again

### Step 4 — Configure the plugin

1. In Calibre, go to **Preferences** > **Plugins**
2. Find **Skrivist** under **User interface action**
3. Click **Customize plugin**
4. Paste your API key into the **API Key** field
5. Click **OK**

The toolbar button **Send to Skrivist** will now appear. You can also add it manually via **Preferences** > **Toolbars & menus**.

---

## Usage

1. Select one or more books in your Calibre library
2. Click **Send to Skrivist** in the toolbar (or press `Ctrl+Shift+K`)
3. Confirm if uploading multiple books
4. Books appear in your Skriv.ist library within seconds

> **Note:** Only EPUB format is supported. Use Calibre's built-in **Convert books** feature to convert other formats to EPUB first.

---

## Requirements

- Calibre 5.0 or newer
- Books must be in EPUB format
- EPUB files must be 50 MB or smaller (the server upload limit)
- A [Skriv.ist](https://skriv.ist) account
- An API key (generated in Skriv.ist settings)

---

## Troubleshooting

**"API Key Required" error**
→ Go to **Preferences** > **Plugins** > **Skrivist** > **Customize plugin** and enter your API key.

**"Book is not in EPUB format" error**
→ Select the book in Calibre, click **Convert books**, set output format to EPUB, then try again.

**Upload fails / network error**
→ Check your internet connection. If the problem persists, verify your API key is still active in Skriv.ist settings.

**Book doesn't appear in library**
→ Refresh the Skriv.ist page. If the book still doesn't appear after 30 seconds, check that your cloud book quota hasn't been reached (free plan: 10 books).

**"Send to Skrivist" button not in toolbar**
→ Go to **Preferences** > **Toolbars & menus** > **The main toolbar**, find **Skrivist** in the left panel and add it.
