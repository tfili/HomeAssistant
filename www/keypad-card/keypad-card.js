const BUTTONS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "clear"];

customElements.whenDefined("card-tools").then(() => {
  var cardTools = customElements.get('card-tools');
  const LitElement = cardTools.LitElement;
  const html = cardTools.LitHtml;
  const css = cardTools.LitCSS;

  class KeypadCard extends LitElement {
    static get properties() {
      return {
        hass: { type: Object },
        code: { type: String },
        card: { type: Object },
        timeout: { type: Number },
        exemptions: { type: Array },
        _unlocked: { type: Boolean },
      };
    }

    static get styles() {
      return css`
        paper-input {
          margin: 0 auto 8px;
          max-width: 150px;
          text-align: center;
        }

        #keypad {
          display: flex;
          justify-content: center;
          flex-wrap: wrap;
          margin: auto;
          width: 100%;
          max-width: 300px;
        }

        #keypad mwc-button {
          padding: 8px;
          width: 30%;
          box-sizing: border-box;
        }

        mwc-button.numberkey {
          --mdc-typography-button-font-size: var(--keypad-font-size, 0.875rem);
        }
      `;
    }

    constructor() {
      super();
      this.hass = undefined;
      this.name = "";
      this.code = "";
      this.card = undefined;
      this.timeout = 0;
      this.exemptions = []; 
      this._input = undefined;
      this._unlocked = false;
      this._timer = undefined;
    }

    render() {
      const exempt = this.exemptions.some(exemption => {
        return this.hass?.user?.name === exemption.username ||
        this.hass?.user?.id === exemption.user
      });
      if (exempt || this._unlocked) {
        return html`
          <ha-card>
            <card-maker .config=${this.card} .hass=${this.hass}></card-maker>
          </ha-card>
        `;
      }

      return html`
        <ha-card>
          <h1 class="card-header">${this.name}</h1>
          <paper-input
            id="code"
            type="password"
            inputmode="numeric"
          ></paper-input>
          <div id="keypad">
            ${BUTTONS.map((value) => {
              return value === ""
                ? html` <mwc-button disabled></mwc-button> `
                : html`
                    <mwc-button
                      .value="${value}"
                      @click="${this._handlePadClick}"
                      outlined
                      class=${value !== "clear" ? "numberkey" : ""}
                      >${value}
                    </mwc-button>
                  `;
            })}
          </div>
        </ha-card>
      `;
    }

    updated() {
      this._input = this.shadowRoot.getElementById("code");
    }

    _handlePadClick(e) {
      const val = e.currentTarget.value;
      this._input.value = val === "clear" ? "" : this._input.value + val;

      if (this._input.value === this.code) {
        this._input.value = "";
        this._unlocked = true;

        if(this._timer) {
          clearTimeout(this._timer);
        }

        if (this.timeout > 0) {
          this._timer = setTimeout(() => {
            this._unlocked = false;
            this._timer = undefined;
          }, this.timeout);
        }
      }
    }

    setConfig(config) {
      if (!config.code) {
        throw new Error("You need to define a code");
      }

      if (!config.card) {
        throw new Error("You need to define a card");
      }

      this.name = config.name || "";
      this.code = config.code.toString();
      this.timeout = config.timeout ? config.timeout * 1000 : 0; // Seconds -> Milliseconds
      this.exemptions = config.exemptions || [];
      this.card = config.card;
    }
  }

  customElements.define("keypad-card", KeypadCard);
});

setTimeout(() => {
  if (customElements.get("card-tools")) return;
  customElements.define(
    "my-plugin",
    class extends HTMLElement {
      setConfig() {
        throw new Error(
          "Can't find card-tools. See https://github.com/thomasloven/lovelace-card-tools"
        );
      }
    }
  );
}, 2000);
