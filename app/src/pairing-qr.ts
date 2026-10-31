export class PairingQrController {
  private generation = 0;

  constructor(
    private readonly isVisible: () => boolean,
    private readonly showImage: (dataUri: string) => void,
    private readonly hideImage: () => void,
  ) {}

  hide(): void {
    this.generation += 1;
    this.hideImage();
  }

  async display(createQr: () => Promise<string>): Promise<boolean> {
    this.hide();
    const requestGeneration = this.generation;
    if (!this.isVisible()) return false;
    let dataUri = await createQr();
    try {
      if (requestGeneration !== this.generation || !this.isVisible()) return false;
      this.showImage(dataUri);
      return true;
    } finally {
      dataUri = '';
    }
  }
}
