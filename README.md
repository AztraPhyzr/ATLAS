# History Atlas

History Atlas is a standalone website. Open `index.html` in a browser; no backend server or Python packages are required.

Accounts, password hashes, and contributed information are stored in that browser only. They do not sync to other devices or visitors. The site keeps its new-and-updated information list alongside locally saved contributions. Clearing the browser's site data removes these accounts and contributions.

Search looks up matching Wikipedia pages and keeps results with a documented historical connection: past events, historical people, places associated with past events, and other articles linked to those events. Searches with unrelated matches show a notice and omit them. Selecting a result shows Wikipedia's lead summary, related articles, images, and a timeline of dated moments drawn from its article text. Curated sample events also show related article links. Selecting a related article or gallery image opens its article within History Atlas rather than navigating to Wikipedia. The page links to the article history and CC BY-SA 4.0 license for text attribution. Wikipedia and Wikidata lookups require an internet connection; content is fetched when requested and is not copied into a local database.

Login and signup use their own full-page account screen. Creating an account requires separately accepting the Privacy Policy, Terms of Service, and End-User License Agreement; each full document is available in an internal dialog. Saving information or timeline events requires confirming the factuality and contribution standards shown in the editor.

The ignored `atlas.sqlite3` files, if present from an earlier version, are retained as legacy data and are not used by the website.
