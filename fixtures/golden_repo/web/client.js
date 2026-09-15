import { format } from "./util.js";

export function renderUser(user) {
  return format(user.name);
}

class Widget {
  draw(ctx) {
    return renderUser(ctx);
  }
}
