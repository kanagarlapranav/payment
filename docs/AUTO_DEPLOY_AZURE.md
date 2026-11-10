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

The Azure B1s VM uses a **dynamic public IP** (`172.198.162.13`) by default.
- If you stop/deallocate the VM in Azure Portal, Azure may release the IP and assign a new one when restarted.
- **If deployment fails to connect:** Check your VM's public IP in the Azure Portal. If it changed, simply update the `AZURE_VM_HOST` secret in GitHub.
- **Permanent fix (optional):** In Azure Portal, navigate to **payment-tracker-vm** > **Networking** > click the Public IP resource > **Configuration** > set Assignment to **Static**.

---

## One-Time Setup Instructions

### Step 1: Generate an SSH Key Pair

Run this command in your PC's terminal (PowerShell or Bash):
```bash
ssh-keygen -t ed25519 -f ~/.ssh/azure_deploy -C "github-actions-azure-deploy"
```
*(Press Enter when prompted for a passphrase to keep it empty for automated logins).*

This creates two files in your `~/.ssh/` folder:
- `~/.ssh/azure_deploy.pub` (Public key — added to the VM)
- `~/.ssh/azure_deploy` (Private key — added to GitHub Secrets)

---

### Step 2: Add Public Key to the Azure VM

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

---

### Step 3: Add Secrets to GitHub Repository

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

## Step 4: Testing the Workflow

You can test the deployment in two ways:
1. **Manual trigger:** Go to GitHub > **Actions** tab > **Deploy to Azure VM** > click **Run workflow** on `main`.
2. **Push trigger:** Push any commit to the `main` branch. GitHub Actions will automatically trigger and run the SSH deploy step.
3. **Verify on the VM:**
   ```bash
   sudo systemctl status payment-tracker
   sudo journalctl -u payment-tracker -n 20 --no-pager
   ```
