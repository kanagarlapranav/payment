# Auto-Deploying Payment Tracker to Azure VM

This guide explains how automated deployments to the Azure VM (`payment-tracker-vm`) work and details the one-time SSH key and GitHub Secrets setup.

---

## How It Works

When you push code changes to the `main` branch of `kanagarlapranav/payment`:
1. **Render** automatically detects the push and redeploys its web service.
2. **GitHub Actions** runs `.github/workflows/deploy-azure.yml`, connects to your Azure VM over SSH, and runs `/home/azureuser/payment/scripts/update_azure.sh`.
3. The update script pulls the new code, updates any changed dependencies from `requirements.txt`, and restarts the `payment-tracker` systemd service.

> [!IMPORTANT]
> **Environment & Data Safety Guarantee:**
> Auto-deploy pushes **code only**. The workflow never modifies, deletes, or overwrites `/home/azureuser/payment/.env` or `/home/azureuser/.payment_tracker/data`. Your production settings (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID=8379948573`, `TELEGRAM_GROUP_ID=-1004310685141`, `GEMINI_API_KEY`, and `GEMINI_MODEL`) remain completely untouched.

---

## ⚠️ Important Note on Dynamic IP

The Azure Standard_B2ts_v2 VM uses a **dynamic public IP** (`172.198.162.13`) by default.
- If you stop/deallocate the VM in Azure Portal, Azure may release the IP and assign a new one when restarted.
- **If deployment fails to connect:** Check your VM's public IP in the Azure Portal. If it changed, simply update the `AZURE_VM_HOST` secret in GitHub.
- **Permanent fix (optional):** In Azure Portal, navigate to **payment-tracker-vm** > **Networking** > click the Public IP resource > **Configuration** > set Assignment to **Static**.

---

## One-Time Cutover Sequence (Steps 1, 2, and 3)

Follow these steps in order to cut over cleanly to the Azure VM:

### Step 1: Configure Environment Variables on the VM (`.env`)
The VM needs your real bot credentials before it can start. Run the interactive configuration script directly on the VM:
```bash
cd ~/payment
git pull origin main
bash scripts/configure_env.sh
```
- It automatically detects placeholder values and prompts you for real values.
- If the `GEMINI_API_KEY` line is missing, it adds it automatically.
- Paste your real `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY` (typing will be hidden).
- It will verify `TELEGRAM_USER_ID=8379948573`, `TELEGRAM_GROUP_ID=-1004310685141`, and `GEMINI_MODEL=gemini-3.6-flash`.
- It preserves every other existing configuration line in `.env`.

> [!CRITICAL]
> You must complete `.env` configuration **before** starting the service; otherwise, the bot cannot authenticate with Telegram.

---

### Step 2: One-Time SSH Key & GitHub Secrets Setup

#### A. Generate an SSH Key Pair on Your PC
Run this command in your PC's terminal (PowerShell or Bash):
```bash
ssh-keygen -t ed25519 -f ~/.ssh/azure_deploy -C "github-actions-azure-deploy"
```
*(Press Enter when prompted for a passphrase to keep it empty for automated logins).*

This creates two files in your `~/.ssh/` folder:
- `~/.ssh/azure_deploy.pub` (Public key — added to the VM)
- `~/.ssh/azure_deploy` (Private key — added to GitHub Secrets)

#### B. Add Public Key to the Azure VM
1. Copy the content of your public key (`~/.ssh/azure_deploy.pub`). On Windows PowerShell:
   ```powershell
   Get-Content ~/.ssh/azure_deploy.pub
   ```
2. SSH into your Azure VM:
   ```bash
   ssh azureuser@172.198.162.13
   ```
3. Append the public key to `~/.ssh/authorized_keys`:
   ```bash
   echo "<PASTE_YOUR_PUBLIC_KEY_CONTENT_HERE>" >> ~/.ssh/authorized_keys
   chmod 600 ~/.ssh/authorized_keys
   chmod 700 ~/.ssh
   ```
4. Test connecting from your PC using the new key without a password:
   ```bash
   ssh -i ~/.ssh/azure_deploy azureuser@172.198.162.13
   ```

#### C. Add Secrets to GitHub Repository
1. Open your GitHub repository in your browser: [https://github.com/kanagarlapranav/payment](https://github.com/kanagarlapranav/payment)
2. Go to **Settings** > **Secrets and variables** > **Actions**.
3. Click **New repository secret** and add each of the following:

| Secret Name | Value | Description |
| :--- | :--- | :--- |
| `AZURE_VM_HOST` | `172.198.162.13` | Your Azure VM public IP address |
| `AZURE_VM_USER` | `azureuser` | Your Azure VM login username |
| `AZURE_VM_SSH_KEY` | *(Content of `~/.ssh/azure_deploy`)* | Entire private key, including `-----BEGIN` and `-----END` lines |
| `AZURE_VM_PORT` | `22` | *(Optional)* SSH port (defaults to 22) |

> [!CAUTION]
> Never commit your private SSH key or paste it into public chat or repository files.

---

### Step 3: First Code Pull on the VM

The VM currently has an older code commit. Before the GitHub Actions workflow takes over, pull the latest code onto the VM once by hand:
```bash
cd ~/payment
git pull origin main
bash scripts/update_azure.sh
```
- This pulls all automation scripts, installs any updated requirements, and restarts the `payment-tracker` service.
- **After this one-time manual pull, you never need to pull by hand again.** Every future push to `main` will automatically deploy through GitHub Actions!

---

## Step 4: Testing the Workflow

You can test the deployment in two ways:
1. **Manual trigger:** Go to GitHub > **Actions** tab > **Deploy to Azure VM** > click **Run workflow** on `main`.
2. **Push trigger:** Push any commit to the `main` branch. GitHub Actions will automatically trigger and run the SSH deploy step.
3. **Verify on the VM:**
   ```bash
   sudo systemctl status payment-tracker
   sudo journalctl -u payment-tracker -n 20 --no-pager
   ```

