import os
import time
import requests
import httpx
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Static repo mode
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
OWNER = os.getenv("GITHUB_OWNER")
REPO = os.getenv("GITHUB_REPO")

# OAuth app credentials
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8000/login/callback"
FRONTEND_URL = "http://localhost:3000"  # Or whatever your frontend URL is

# Verify required env vars
if not all([GITHUB_TOKEN, OWNER, REPO]):
    raise RuntimeError("Missing one or more required environment variables: GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO")

# Base repo for static endpoints
BASE_URL = f"https://api.github.com/repos/{OWNER}/{REPO}"
HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}

app = FastAPI()

# CORS settings for React frontend
origins = [
    "http://localhost:3000",  # React dev server
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Helper: GitHub GET with rate limit handling ---
def github_get(url, params=None):
    response = requests.get(url, headers=HEADERS, params=params)
    if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
        reset_time = int(response.headers.get("X-RateLimit-Reset", 0))
        wait_seconds = reset_time - int(time.time()) + 5
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded. Retry after {wait_seconds} seconds.")
    elif response.status_code != 200:
        try:
            error_detail = response.json()
        except Exception:
            error_detail = response.text
        raise HTTPException(status_code=response.status_code, detail=error_detail)
    return response.json()

# --- Static Repo Endpoints (Your Original) ---
@app.get("/prs")
def get_open_pull_requests(page: int = Query(1, ge=1), per_page: int = Query(30, ge=1, le=100)):
    url = f"{BASE_URL}/pulls"
    params = {"state": "open", "page": page, "per_page": per_page}
    prs = github_get(url, params=params)
    return [
        {
            "number": pr["number"],
            "title": pr["title"],
            "user": pr["user"]["login"],
            "created_at": pr["created_at"],
            "url": pr["html_url"]
        }
        for pr in prs
    ]

@app.get("/issues")
def get_open_issues(page: int = Query(1, ge=1), per_page: int = Query(30, ge=1, le=100)):
    url = f"{BASE_URL}/issues"
    params = {"state": "open", "page": page, "per_page": per_page}
    issues = github_get(url, params=params)
    filtered_issues = [issue for issue in issues if "pull_request" not in issue]
    return [
        {
            "number": issue["number"],
            "title": issue["title"],
            "user": issue["user"]["login"],
            "created_at": issue["created_at"],
            "url": issue["html_url"]
        }
        for issue in filtered_issues
    ]

@app.get("/commits")
def get_commits(page: int = Query(1, ge=1), per_page: int = Query(30, ge=1, le=100)):
    url = f"{BASE_URL}/commits"
    params = {"page": page, "per_page": per_page}
    commits = github_get(url, params=params)
    return [
        {
            "sha": commit["sha"],
            "author": commit["commit"]["author"]["name"],
            "message": commit["commit"]["message"],
            "date": commit["commit"]["author"]["date"],
            "url": commit["html_url"]
        }
        for commit in commits
    ]

# --- OAuth GitHub Login ---
@app.get("/login")
def github_login():
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope=repo"
        f"&prompt=consent"
    )
    return RedirectResponse(url)

@app.get("/login/callback")
async def login_callback(request: Request):
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing code parameter")

    async with httpx.AsyncClient() as client:
        # Exchange code for access token
        token_res = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
        )
        token_data = token_res.json()
        access_token = token_data.get("access_token")

        if not access_token:
            raise HTTPException(status_code=400, detail="Failed to get access token")

        # Get user info
        user_res = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user_data = user_res.json()

    # Redirect user to frontend, pass info as query params
    redirect_url = (
        f"{FRONTEND_URL}"
        f"?access_token={access_token}"
        f"&username={user_data.get('login')}"
        f"&avatar_url={user_data.get('avatar_url')}"
        f"&profile={user_data.get('html_url')}"
    )
    return RedirectResponse(redirect_url)

# --- Dynamic User Repos (With token) ---
@app.get("/user/repos")
async def get_user_repos(token: str = Query(...)):
    async with httpx.AsyncClient() as client:
        res = await client.get(
            "https://api.github.com/user/repos",
            headers={"Authorization": f"token {token}"}
        )
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail=res.text)
        data = res.json()

        return [
            {
                "name": repo["name"],
                "owner": repo["owner"]["login"],
                "url": repo["html_url"]
            }
            for repo in data
        ]
