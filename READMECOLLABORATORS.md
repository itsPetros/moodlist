
## 🛠️ Collaborator Setup Guide

Because the local SQLite database (`moodlist.db`) and private environmental variables (`.env`) are ignored by Git for security and stability, you must initialize your own local instance upon cloning the repository.

### 1. Initialize Virtual Environment & Install Dependencies
Open your terminal in the project root directory and run the following commands:

```bash
# Create a virtual environment
python -m venv .venv

# Activate the virtual environment
# On Windows:
.venv\Scripts\activate
# On Mac/Linux:
source .venv/bin/activate

# Install required packages
pip install -r requirements.txt
```

### 2. Configure Environment Variables
1. Copy the template configuration file:
   ```bash
   cp .env.example .env
   ```
2. Open the newly created `.env` file and fill in any required development API keys or configuration secrets.

### 3. Generate Local Database
To populate your local `moodlist.db` file with the core song catalog, execute the build script:
```bash
python build_catalog.py
```

### 4. Run the Application Locally
Start the local development server:
```bash
uvicorn main:app --reload
```
Open your browser and navigate to `http://127.0.0.1:8000`.

---

## 🔄 Git Workflow & Collaboration Rules

To ensure the production application remains completely stable on Render, **do not push commits directly to the `main` branch.** All feature updates, bug fixes, and experiments must be isolated inside sandboxed feature branches.

### Step 1: Sync Your Local Main Branch
Before starting any new work, always ensure you have the latest updates from the team:
```bash
git checkout main
git pull origin main
```

### Step 2: Create a Feature Branch
Spin up a new branch dedicated exclusively to the task you are working on. Use a descriptive naming convention:
```bash
git checkout -b feature/your-feature-name
# Example: git checkout -b feature/user-login
# Example: git checkout -b bugfix/fix-playlist-button
```

### Step 3: Code and Commit Locally
Work on your feature within your sandbox branch. Group logical blocks of work into clean commits:
```bash
git add .
git commit -m "Add descriptive message about what changed"
```

### Step 4: Push the Branch to GitHub
When your feature is fully completed and tested locally, push your branch up to the remote repository:
```bash
git push -u origin feature/your-feature-name
```

### Step 5: Submit a Pull Request
1. Go to the project repository page on GitHub.
2. Click the green **Compare & pull request** button next to your recently pushed branch.
3. Write a brief summary explaining what your code changes accomplish.
4. Submit the Pull Request for review. 

*The repository owner will review the changed files and merge them into the active production branch once approved.*
