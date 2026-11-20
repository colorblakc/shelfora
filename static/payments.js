// Fine payments: opens the payment window, then asks the server to confirm the result.
async function paymentRequest(path, values) {
  const data = new FormData();
  data.set("_token", document.querySelector('meta[name="csrf-token"]').content);
  Object.entries(values).forEach(([key, value]) => data.set(key, value));
  const response = await fetch(path, {method:"POST", body:data});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Please try again.");
  return result;
}
document.querySelectorAll(".pay-fine").forEach(button => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      if (typeof Razorpay !== "function") throw new Error("Checkout could not load. Check your connection and try again.");
      const order = await paymentRequest("/payments/start", {kind:button.dataset.kind, fine_id:button.dataset.id});
      const checkout = new Razorpay({
        key:order.key, amount:order.amount, currency:order.currency, order_id:order.order_id,
        name:"Shelfora", description:"Library fine",
        handler: async result => {
          try {
            await paymentRequest("/payments/verify", {order_id:result.razorpay_order_id,
              payment_id:result.razorpay_payment_id, signature:result.razorpay_signature});
            window.location.reload();
          } catch (error) {
            alert(error.message + " Use Check payment status to confirm before trying again.");
            window.location.reload();
          }
        },
        modal:{ondismiss:() => {button.disabled = false; window.location.reload();}}
      });
      checkout.on("payment.failed", () => {
        alert("The payment did not complete. You can check its status or try again.");
        window.location.reload();
      });
      checkout.open();
    } catch (error) {
      alert(error.message);
      button.disabled = false;
    }
  });
});
document.querySelectorAll(".check-fine").forEach(button => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await paymentRequest("/payments/check", {kind:button.dataset.kind, fine_id:button.dataset.id});
      window.location.reload();
    } catch (error) {
      alert(error.message);
      button.disabled = false;
    }
  });
});
