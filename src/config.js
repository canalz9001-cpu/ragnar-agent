export const config = {
  port: Number(process.env.PORT || 3000),
  verifyToken: process.env.WEBHOOK_VERIFY_TOKEN || "",
  metaAppSecret: process.env.CLIENT_RAGNAR_META_APP_SECRET || "",
  instagramAccessToken: process.env.CLIENT_RAGNAR_INSTAGRAM_ACCESS_TOKEN || "",
  instagramAccountId: process.env.CLIENT_RAGNAR_INSTAGRAM_ACCOUNT_ID || "",
  graphVersion: process.env.META_GRAPH_VERSION || "v24.0",
  website: "https://ragnarplay.online/",
  whatsappDisplay: "(34) 9134-1688",
  whatsappUrl: "https://wa.me/553491341688",
  greeting:
    "Olá! 👋 Sou o Ragnar, assistente virtual da Ragnar One. Vi seu comentário e vim te ajudar! Você pode conhecer os planos e solicitar seu teste grátis pelo site: https://ragnarplay.online/\n\nSe preferir, fale com nossa equipe pelo WhatsApp: (34) 9134-1688 👉 https://wa.me/553491341688\n\nTemos planos a partir de R$ 25,00, com 2 dispositivos simultâneos. Você quer testar ou conhecer os planos?"
};
