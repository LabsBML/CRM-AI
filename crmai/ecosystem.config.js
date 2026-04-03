module.exports = {
  apps: [
    {
      name: "n8n",
      script: "/usr/bin/n8n",
      args: "start",
      interpreter: "/usr/bin/node",
      env: {
        N8N_HOST: "0.0.0.0",
        N8N_PORT: "5678",


        N8N_PROTOCOL: "https",
        WEBHOOK_URL: "https://hippopotamic-unpompous-liz.ngrok-free.dev",


        N8N_SECURE_COOKIE: "false"
      }
    },

    {
      name: "ngrok",
      script: "/usr/local/bin/ngrok",
      args: "http 5678",
      autorestart: true,
      watch: false
    }
  ]
};

