const maps = [
  { matches: /^(kavkazi|bakurani)$/i, name: "BAKURANI", image: "kavkazi.webp" },
  { matches: /^(europe|ozeti)$/i, name: "OZETI", image: "europe.webp" },
  {
    matches: /^(northamerica|zestafona)$/i,
    name: "ZESTAFONA",
    image: "north-america.webp"
  }
];

/** Map artwork is illustrative; it does not represent the current lighting. */
export function mapArtwork(map: string | null | undefined) {
  const entry = map
    ? maps.find(item => item.matches.test(map.trim()))
    : undefined;
  return {
    name: entry?.name ?? map ?? "—",
    image: entry ? `${import.meta.env.BASE_URL}maps/${entry.image}` : null
  };
}
